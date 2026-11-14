"""Event-based LUMAX controller cycle model (INT16 or FP16, signed W8/W4/W2).

Stage scheduling follows LUMAX_Top.scala and LUMAXTopHelpers.scala. Memory is
an explicit environment model: HS retains the registered-reference cache-hit
latency. TileLink returns one cycle earlier; queued head responses can bypass
the register. Early/out-of-order responses still occupy the existing buffers.
Selection columns overlap through four valid/tag stages; consumed weight buffers
become reusable after the final stage-2 capture, before accumulation retires.
Weight requests advance to the next reserved buffer after the final request is
issued, while earlier responses are still pending. The final response marks its
buffer ready on the same edge; selection can start on the following cycle.
The magic-memory LLC environment includes full-line refill bandwidth, merged
miss queues, one shared SourceD service port and approximate SRAM-bank hazards.
Its effective secondary-request interval is calibrated from DMA transaction
traces, not from stage-cycle totals. It is not a cycle-exact model of the LLC:
round-robin arbitration, coherence, CPU traffic and initial ownership remain
sources of uncertainty. BRAM output prefetch and DMA-credit timing are explicit.
"""
from collections import OrderedDict
import math

MODEL_VERSION = 'controller-events-v7'


def _integer(name, value, minimum=1):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) != value or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')
    return int(value)


def dma_finish_time_exact_bits(total_bits, ID, t_issue=1, L=6, DMA_WIDTH_bits=64, beat_time=1):
    """Last response edge; first issue is edge zero. Released IDs return next edge.

    Responses are ordered and spaced by beat_time. This is exact for the stated
    finite-credit interface, not a claim of fixed-latency cache/DRAM behavior.
    """
    total_bits = _integer('total_bits', total_bits, 0)
    ID, t_issue = _integer('ID', ID), _integer('t_issue', t_issue)
    L = _integer('L', L, 0)
    width, beat_time = _integer('DMA_WIDTH_bits', DMA_WIDTH_bits), _integer('beat_time', beat_time)
    count = (total_bits+width-1)//width
    responses, issued = [], -t_issue
    for i in range(count):
        issued = max(issued+t_issue, responses[i-ID]+1 if i>=ID else 0)
        responses.append(max(issued+L, responses[-1]+beat_time if responses else 0))
    return responses[-1] if responses else 0


def _read_packets(length, source_element, bank_span, precision, width, base):
    """calcChunkInfo_A + packedReadCount; source beats can be reread at bank words."""
    per_word, consumed = width//precision, 0
    while consumed<length:
        linear = source_element+consumed
        count = min(length-consumed, per_word-linear%per_word)
        local = consumed%bank_span
        word_remaining = per_word-local%per_word
        if word_remaining<bank_span-local:
            count = min(count,word_remaining)
        yield base+(linear//per_word)*(width//8),count
        consumed += count


def _store_packets(row_start, active_rows, columns, base):
    """Row-ordered 32-bit outputs; partial words stay separate across rows."""
    for row in range(row_start, row_start+active_rows):
        written = 0
        while written<columns:
            linear = row*columns+written
            count = min(columns-written, 2-linear%2)
            yield base+(linear//2)*8, count, dict(row=row)
            written += count


class _Memory:
    """Approximate LLC/DMA environment, independent of stage-cycle counters.

    The current Chipyard profile has four 8-byte SRAM subbanks and an 8-beat
    64-byte refill. A cache line is not usable by all secondary requests when
    its first response returns. Warm hits keep HS-1 latency. Miss-head wormhole
    service and refill completion are separate; merged requests serialize.
    secondary_cycles is an effective interface calibration (default two), not
    an assertion that every real MSHR response is exactly two cycles apart.
    """
    def __init__(self, hs, profile, line_bytes, cache_bytes, read_penalty,
                 write_penalty, latencies=None, delays=None, trace=False,
                 bypass=True, latency_basis='controller', secondary_cycles=2, subbanks=4, full_writes=True):
        self.hs, self.profile, self.line_bytes = hs, profile, line_bytes
        self.secondary_cycles, self.subbanks, self.full_writes = secondary_cycles, subbanks, full_writes
        self.lines = max(1,cache_bytes//line_bytes)
        self.read_penalty, self.write_penalty = read_penalty,write_penalty
        self.cache = OrderedDict()
        self.latencies, self.delays = latencies,delays
        self.bypass = bypass
        self.controller_replay = latencies is not None and latency_basis=='controller'
        self.index = self.reads = self.writes = self.misses = 0
        self.trace = [] if trace else None
        # One DMA engine spans all columns and stages. Retain its outstanding
        # source credits and D-channel reservations across transfer() calls.
        self.responses = []
        self.return_slots = set()
        self.last_issue = -1
        self.refill_end = -1
        self.line_service = {}
        self.bank_slots = set()
        self.service_slots = set()

    def response(self, address, write, issued, full_write=False):
        i = self.index
        if i and i%4096==0:
            cutoff=issued-2*self.hs
            self.bank_slots={slot for slot in self.bank_slots if slot[0]>=cutoff}
            self.service_slots={slot for slot in self.service_slots if slot>=cutoff}
            self.return_slots.intersection_update(slot for slot in self.return_slots if slot>=cutoff)
        self.index += 1
        self.writes += bool(write)
        self.reads += not write
        if self.latencies is not None:
            if i>=len(self.latencies):
                raise ValueError('DMA replay ran out of response latencies')
            return issued+_integer('DMA replay latency',self.latencies[i],0)
        # Preserve the calibrated memory costs: HS includes the old receiver
        # register. Model the physical return separately, not a faster memory.
        hit = issued+self.hs-1
        if self.profile=='uniform':
            return hit
        line = address//self.line_bytes
        ready = self.cache.pop(line,None)
        if ready is None:
            self.misses += 1
            # Outer refill D channel is 64 bits in LUMAXROcketConfig.
            beats = self.line_bytes//8
            # read_penalty historically includes the seven tail beats;
            # write_penalty describes the first acknowledgement. Preserve those
            # public meanings while reserving the whole physical refill burst.
            first_latency = self.hs-1+(self.write_penalty if write else max(0,self.read_penalty-(beats-1)))
            first = max(issued+first_latency,self.refill_end+1)
            self.refill_end = first+beats-1
            ready = self.refill_end+1
            # BankedStore gives incoming refill writes priority over SourceD
            # reads. Represent their port occupancy; this is approximate because
            # future arbitration can also delay already-pending operations.
            for beat in range(beats):
                self.bank_slots.add((first-(self.hs-1)+beat,beat%self.subbanks))
            response = first if address%self.line_bytes==0 else self.refill_end
            if len(self.cache)>=self.lines:
                self.cache.popitem(last=False)
        else:
            response = max(hit,ready)
        self.cache[line] = ready
        # SourceD has one shared request pipeline. Its SRAM read is four
        # interface cycles before the modeled response; writeback follows three
        # stages after the read. These offsets describe this Chipyard profile,
        # not a portable DRAM timing model.
        service=max(response-4,self.line_service.get(line,-1))
        bank=(address//8)%self.subbanks
        needs_read = not (write and full_write and self.full_writes)
        while service in self.service_slots or (needs_read and (service,bank) in self.bank_slots):
            service+=1
        self.service_slots.add(service)
        if needs_read:
            self.bank_slots.add((service,bank))
        if write:
            self.bank_slots.add((service+3,bank))
        # Requests merged behind a refill have an effective reload interval;
        # an already-warm line can continue at one request per cycle.
        self.line_service[line]=service+(self.secondary_cycles if hit<ready else 1)
        return service+4

    def transfer(self, packets, start, ids, write=False, context=None, prefetch_latency=0, prefetch_depth=0):
        responses, return_slots = self.responses,self.return_slots
        issued = max(start-1,self.last_issue)
        last = None
        local_issues = []
        for packet in packets:
            address,count = packet[:2]
            issued = max(issued+1,responses[-ids]+1 if len(responses)>=ids else start)
            if self.delays is not None:
                if self.index>=len(self.delays):
                    raise ValueError('DMA replay ran out of issue delays')
                issued += _integer('DMA replay issue delay',self.delays[self.index],0)
            if prefetch_depth:
                # SyncReadMem read -> queue enqueue -> non-flow dequeue.
                # A full queue frees a reservation only after an accepted A beat.
                data_ready=start+len(local_issues)+prefetch_latency
                if len(local_issues)>=prefetch_depth:
                    data_ready=max(data_ready,local_issues[-prefetch_depth]+1+prefetch_latency)
                issued=max(issued,data_ready)
            local_issues.append(issued)
            ready = self.response(address,write,issued,full_write=write and count==2)
            if self.controller_replay:
                # Existing diagnostic arrays already describe io.d_valid.
                # Do not subtract a receiver cycle from measured delivery times.
                arrival, bypassed = None,None
                delivered = max(ready,responses[-1]+1 if responses else ready)
            else:
                # A single TileLink D channel accepts one return per cycle.
                # Earlier-issued requests reserve contended return slots first;
                # later requests may otherwise return early/out of order.
                arrival = ready
                while arrival in return_slots:
                    arrival += 1
                return_slots.add(arrival)
                # Queue entries exist after issue; a released head/source is
                # reusable on the next edge. A same-cycle new request/response
                # into an empty queue must be buffered, even with bypass on.
                head_ready = max(issued+1,responses[-1]+1 if responses else start)
                bypassed = self.bypass and arrival>=head_ready
                delivered = max(arrival if bypassed else arrival+1,head_ready)
            responses.append(delivered)
            self.last_issue = issued
            last = delivered
            if self.trace is not None:
                # Optional per-packet metadata preserves rows in a shared transfer.
                self.trace.append(dict(context or {},**(packet[2] if len(packet)>2 else {}),address=address,write=write,
                                       elements=count,issue=issued,response=delivered,
                                       tilelink_response=arrival,bypassed=bypassed))
        return last if last is not None else start-1


def compute_cycles(Rin, Cin, Cout, in_bits, w_bits, XS, YS, ID,
                   buffers_w=2, factor=4, DMA_WIDTH=64,
                   extra_prefetch_cycles=0, cache_8_4=False, cache_reads=1,
                   IN_MAX=16, IN_min=8, W_MAX=8, HS=7,
                   activation='int16', memory_profile='chipyard_llc',
                   cache_line_bytes=64, cache_bytes=512*1024,
                   read_miss_penalty=11, write_miss_penalty=4,
                   x_address=0x10000000, w_address=0x20000000,
                   o_address=0x30000000, block_size=None, trace=False,
                   dma_latencies=None, dma_issue_delays=None,
                   dma_bypass=True, dma_latency_basis='controller',
                   bram_outputs=True, dma_full_writes=True,
                   llc_secondary_cycles=2, llc_subbanks=4):
    """Predict active accelerator cycles; no CPU, printing, wall-clock or startup.

    XS=b, factor=RF, YS=elaborated row parallelism, Rin=actual rows. FP16
    significand, alignment and FP32 conversion are combinational in this RTL:
    they affect area/Fmax, not this controller's number of cycles.

    bram_outputs=True matches the optimized OutputAccumulator: one synchronous
    read plus a non-flow queue, four words including the pending read. False
    models the older output register array. dma_full_writes=True matches the
    PutFullData optimization; set False for historical PutPartial-only logs.
    llc_secondary_cycles (default 2) is an effective transaction-trace calibration
    for merged LLC misses. It is an exposed memory-environment assumption, not
    a fitted stage multiplier or a claim of exact inclusive-cache simulation.

    HS keeps its registered-reference meaning (default 7); the unchanged
    memory path returns at HS-1 (default 6). dma_bypass=False models the old
    registered receiver. The default forwards only an already-queued head.

    Base-address defaults describe disjoint cache-line-aligned buffers. Pass
    saved physical addresses to model actual beat offsets and line sharing.
    The LLC profile assumes cold buffers and no CPU interference. Diagnostic
    dma_latencies/dma_issue_delays replay only external DMA behavior; they never
    take HW stage/total counters, and are not independent accuracy predictions.
    Latency replay defaults to already controller-visible values for backwards
    compatibility. Use dma_latency_basis='tilelink' for physical D-channel
    return latencies, which exercise bypass and ordered buffering explicitly.
    cache_8_4/cache_reads are compatibility arguments for unsupported narrow-X
    experiments; they do not change the implemented input16 datapath.
    """
    names=('Rin','Cin','Cout','XS','YS','ID','buffers_w','factor','DMA_WIDTH')
    vals=dict(zip(names,(Rin,Cin,Cout,XS,YS,ID,buffers_w,factor,DMA_WIDTH)))
    Rin,Cin,Cout,XS,YS,ID,buffers_w,factor,DMA_WIDTH=(_integer(k,vals[k]) for k in names)
    if in_bits!=16 or IN_MAX!=16 or W_MAX!=8 or w_bits not in (2,4,8):
        raise ValueError('Model supports input16 and W8/W4/W2 (IN_MAX=16, W_MAX=8)')
    if activation not in ('int16','fp16'):
        raise ValueError('activation must be int16 or fp16')
    if DMA_WIDTH!=64:
        raise ValueError('Current RTL load/store model requires DMA_WIDTH=64')
    if memory_profile not in ('uniform','chipyard_llc'):
        raise ValueError('memory_profile must be uniform or chipyard_llc')
    if not isinstance(dma_bypass,bool):
        raise ValueError('dma_bypass must be a bool')
    if dma_latency_basis not in ('controller','tilelink'):
        raise ValueError('dma_latency_basis must be controller or tilelink')
    if not isinstance(bram_outputs,bool) or not isinstance(dma_full_writes,bool):
        raise ValueError('bram_outputs and dma_full_writes must be bools')
    secondary=_integer('llc_secondary_cycles',llc_secondary_cycles)
    subbanks=_integer('llc_subbanks',llc_subbanks)
    HS=_integer('HS',HS)
    prefetch_delay=_integer('extra_prefetch_cycles',extra_prefetch_cycles,0)
    line_bytes=_integer('cache_line_bytes',cache_line_bytes)
    cache_bytes=_integer('cache_bytes',cache_bytes)
    if line_bytes<8 or line_bytes%8:
        raise ValueError('cache_line_bytes must be a positive multiple of 8')
    read_penalty=_integer('read_miss_penalty',read_miss_penalty,0)
    write_penalty=_integer('write_miss_penalty',write_miss_penalty,0)
    for name,address in [('x_address',x_address),('w_address',w_address),('o_address',o_address)]:
        _integer(name,address,0)
        if address%8:
            raise ValueError(name+' must be DMA-beat aligned')
    memory=_Memory(HS,memory_profile,line_bytes,cache_bytes,read_penalty,
                   write_penalty,dma_latencies,dma_issue_delays,trace,
                   bypass=dma_bypass,latency_basis=dma_latency_basis,
                   secondary_cycles=secondary,subbanks=subbanks,full_writes=dma_full_writes)
    capacity=factor*(1<<(W_MAX-w_bits))
    window_size=XS*capacity
    if block_size is not None:
        window_size=_integer('block_size',block_size)
        if window_size>XS*capacity:
            raise ValueError('block_size exceeds physical LUT capacity')
    lut_rows=1<<(w_bits-2)
    now=load_x=generation=combined=store_o=load_w=selection=windows=0
    first_producer=first_select=None
    window_trace=[] if trace else None
    column_trace=[] if trace else None
    for row_start in range(0,Rin,YS):
        active_rows=min(YS,Rin-row_start)
        bank_span=min(capacity,(min(Cin,window_size)+XS-1)//XS)
        for k_start in range(0,Cin,window_size):
            length=min(window_size,Cin-k_start)
            windows+=1
            before_x=now
            for row in range(row_start,row_start+active_rows):
                packets=_read_packets(length,row*Cin+k_start,bank_span,16,DMA_WIDTH,x_address)
                last=memory.transfer(packets,now,ID,context=dict(stage='load_x',row=row,k_start=k_start))
                # Final response, then row completion; one final state change.
                now=last+2
            now+=1
            load_x+=now-before_x
            gen_start=now
            gen_cycles=bank_span*(lut_rows+1)
            generation+=gen_cycles
            select_available=gen_start+gen_cycles
            producer_available=gen_start+prefetch_delay
            released_columns=[]
            last_retire=select_available
            select_cycles=bank_span+4
            for col in range(Cout):
                start=max(producer_available,released_columns[col-buffers_w] if col>=buffers_w else gen_start)
                packets=_read_packets(length,col*Cin+k_start,bank_span,w_bits,DMA_WIDTH,w_address)
                last=memory.transfer(packets,start,ID,context=dict(stage='load_w',column=col,k_start=k_start,row_group=row_start))
                # The final response writes SRAM and marks the reserved buffer
                # ready on the same edge; selection sees it next cycle.
                ready=last+1
                # Per-column latency intervals can overlap; this diagnostic
                # sum is not an exclusive stage duration. Total cycles use
                # the combined producer/selection schedule below.
                load_w+=ready-start
                if first_producer is None:
                    first_producer,first_select=ready-start,select_cycles
                # The independent request cursor can fill the next buffer
                # before this column's ordered responses have all returned.
                producer_available=memory.last_issue+1
                begin_select=max(select_available,ready)
                read_end=begin_select+bank_span
                # The next column can issue on the edge after the final input.
                select_available=read_end
                # Stage 2 captures the last weight on that edge; its buffer-mode
                # bit clears for the following cycle. This is not a zero-delay
                # release and does not wait for the four-cycle accumulator tail.
                buffer_release=read_end+1
                released_columns.append(buffer_release)
                retire_end=read_end+4
                # PC_Select counts cycles with an input or any valid tag. Count
                # the union of occupied intervals, not overlapping tails twice.
                selection+=retire_end-max(begin_select,last_retire)
                if column_trace is not None:
                    column_trace.append(dict(row_group=row_start,k_start=k_start,
                        column=col,weight_ready=ready,issue_start=begin_select,
                        read_end=read_end,buffer_release=buffer_release,
                        retire=retire_end))
                last_retire=retire_end
            # The last tagged contribution writes O_reg on the transition edge.
            # There is no additional Done_acc/w_idx handoff cycle in this RTL.
            now=last_retire
            combined+=now-(gen_start+gen_cycles)
            if window_trace is not None:
                window_trace.append(dict(row_group=row_start,k_start=k_start,k=length,
                    bank_span=bank_span,generation=gen_cycles,
                    loadw_select=now-(gen_start+gen_cycles),select_per_column=select_cycles,
                    column_issue_interval=bank_span))
            if k_start+window_size<Cin:
                if YS==1:
                    # Literal current RTL: this uses Cin - b*bank_span, NOT
                    # remaining K. With >=3 windows the final span may stay padded.
                    bank_span=min(bank_span,(Cin-bank_span*XS+XS-1)//XS)
                else:
                    remaining=min(window_size,Cin-k_start-window_size)
                    bank_span=min(bank_span,(remaining+XS-1)//XS)
        before_store=now
        # One credit pool spans the active rows: accepted writes can advance to
        # the next row while earlier acknowledgements are still outstanding.
        packets=_store_packets(row_start,active_rows,Cout,o_address)
        last=memory.transfer(packets,now,ID,write=True,
                             context=dict(stage='store_o',row_group=row_start),
                             prefetch_latency=2 if bram_outputs else 0,
                             prefetch_depth=4 if bram_outputs else 0)
        # Final delivery updates the ack count/empty flag, then the next edge
        # leaves sStoreOutput. There is no per-row wait or completion bubble.
        now=last+2
        store_o+=now-before_store
    if dma_latencies is not None and memory.index!=len(dma_latencies):
        raise ValueError(f'DMA replay consumed {memory.index}/{len(dma_latencies)} responses')
    total=load_x+generation+combined+store_o
    assert total==now
    result={
        'Repeat activation Window':windows,
        'cycles_load_X':load_x,'cycles_generate_Products':generation,
        'cycles_loadW_select':combined,'cycles_store_output':store_o,
        'total_cycles':total,'cycles load w Only':load_w,'cycles select Only':selection,
        'cycles_3_1':first_producer,'cycles_3_2':first_select,
        'LoadOp':memory.reads,'WriteOp':memory.writes,
        'AddOp':(generation+selection)*XS*min(YS,Rin),
        'ShiftOp':Rin*Cin+selection*XS*min(YS,Rin),
        'ReadSramOp':(generation+selection)*XS*min(YS,Rin),
        'WriteSramOp':memory.reads+generation*XS*min(YS,Rin),
        'model_version':MODEL_VERSION,'activation':activation,
        'selection_pipeline_latency':4,'weight_buffer_release_delay':1,
        'dma_response_bypass':dma_bypass,'dma_latency_basis':dma_latency_basis,
        'bram_outputs':bram_outputs,'dma_full_writes':dma_full_writes,
        'memory_profile':memory_profile,
        'memory_assumptions':dict(hit_response_cycles=HS,read_miss_penalty=read_penalty,
            hit_latency_basis='registered_reference',tilelink_hit_response_cycles=HS-1,
            dma_response_bypass=dma_bypass,
            write_miss_penalty=write_penalty,cache_line_bytes=line_bytes,
            initially_cold=True,cpu_interference=False,
            refill_beats=line_bytes//8,llc_subbanks=subbanks,
            secondary_service_cycles=secondary,
            secondary_service_basis='effective interface calibration; not exact LLC RTL',
            full_write_opcode=dma_full_writes),
        'cache_misses':memory.misses,'dma_requests':memory.index,
        'diagnostic_dma_replay':dma_latencies is not None or dma_issue_delays is not None,
    }
    if trace:
        result.update(transactions=memory.trace,windows=window_trace,columns=column_trace)
    return result
