"""Event-based LUMAX controller cycle model (INT16 or FP16, signed W8/W4/W2).

Stage scheduling follows LUMAX_Top.scala and LUMAXTopHelpers.scala. Memory is
an explicit environment model: HS is accepted-request to controller-visible
cache-hit response latency. The current ordered DMA register is included.
Selection columns overlap through four valid/tag stages; consumed weight buffers
become reusable after the final stage-2 capture, before accumulation retires.
The default magic-memory LLC profile uses interface-observed hit/miss costs;
CPU traffic and unknown initial cache ownership remain sources of uncertainty.
"""
from collections import OrderedDict
import math

MODEL_VERSION = 'controller-events-v4'


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
    """DMA environment, independent of accelerator-stage counters."""
    def __init__(self, hs, profile, line_bytes, cache_bytes, read_penalty,
                 write_penalty, latencies=None, delays=None, trace=False):
        self.hs, self.profile, self.line_bytes = hs, profile, line_bytes
        self.lines = max(1,cache_bytes//line_bytes)
        self.read_penalty, self.write_penalty = read_penalty,write_penalty
        self.cache = OrderedDict()
        self.latencies, self.delays = latencies,delays
        self.index = self.reads = self.writes = self.misses = 0
        self.trace = [] if trace else None

    def response(self, address, write, issued):
        i = self.index
        self.index += 1
        self.writes += bool(write)
        self.reads += not write
        if self.latencies is not None:
            if i>=len(self.latencies):
                raise ValueError('DMA replay ran out of response latencies')
            return issued+_integer('DMA replay latency',self.latencies[i],0)
        if self.profile=='uniform':
            return issued+self.hs
        line = address//self.line_bytes
        ready = self.cache.pop(line,None)
        if ready is None:
            self.misses += 1
            ready = issued+self.hs+(self.write_penalty if write else self.read_penalty)
            if len(self.cache)>=self.lines:
                self.cache.popitem(last=False)
        self.cache[line] = ready
        return max(issued+self.hs,ready)

    def transfer(self, packets, start, ids, write=False, context=None):
        responses, issued = [],start-1
        for packet in packets:
            address,count = packet[:2]
            issued = max(issued+1,responses[-ids]+1 if len(responses)>=ids else start)
            if self.delays is not None:
                if self.index>=len(self.delays):
                    raise ValueError('DMA replay ran out of issue delays')
                issued += _integer('DMA replay issue delay',self.delays[self.index],0)
            ready = self.response(address,write,issued)
            delivered = max(ready,responses[-1]+1 if responses else ready)
            responses.append(delivered)
            if self.trace is not None:
                # Optional per-packet metadata preserves rows in a shared transfer.
                self.trace.append(dict(context or {},**(packet[2] if len(packet)>2 else {}),address=address,write=write,
                                       elements=count,issue=issued,response=delivered))
        return responses[-1] if responses else start-1


def compute_cycles(Rin, Cin, Cout, in_bits, w_bits, XS, YS, ID,
                   buffers_w=2, factor=4, DMA_WIDTH=64,
                   extra_prefetch_cycles=0, cache_8_4=False, cache_reads=1,
                   IN_MAX=16, IN_min=8, W_MAX=8, HS=7,
                   activation='int16', memory_profile='chipyard_llc',
                   cache_line_bytes=64, cache_bytes=512*1024,
                   read_miss_penalty=11, write_miss_penalty=4,
                   x_address=0x10000000, w_address=0x20000000,
                   o_address=0x30000000, block_size=None, trace=False,
                   dma_latencies=None, dma_issue_delays=None):
    """Predict active accelerator cycles; no CPU, printing, wall-clock or startup.

    XS=b, factor=RF, YS=elaborated row parallelism, Rin=actual rows. FP16
    significand, alignment and FP32 conversion are combinational in this RTL:
    they affect area/Fmax, not this controller's number of cycles.

    Base-address defaults describe disjoint cache-line-aligned buffers. Pass
    saved physical addresses to model actual beat offsets and line sharing.
    The LLC profile assumes cold buffers and no CPU interference. Diagnostic
    dma_latencies/dma_issue_delays replay only external DMA behavior; they never
    take HW stage/total counters, and are not independent accuracy predictions.
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
    HS=_integer('HS',HS)
    prefetch_delay=_integer('extra_prefetch_cycles',extra_prefetch_cycles,0)
    line_bytes=_integer('cache_line_bytes',cache_line_bytes)
    cache_bytes=_integer('cache_bytes',cache_bytes)
    read_penalty=_integer('read_miss_penalty',read_miss_penalty,0)
    write_penalty=_integer('write_miss_penalty',write_miss_penalty,0)
    for name,address in [('x_address',x_address),('w_address',w_address),('o_address',o_address)]:
        _integer(name,address,0)
        if address%8:
            raise ValueError(name+' must be DMA-beat aligned')
    memory=_Memory(HS,memory_profile,line_bytes,cache_bytes,read_penalty,
                   write_penalty,dma_latencies,dma_issue_delays,trace)
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
                # Response -> dma_resp cleared -> buffer ready (two edges).
                ready=last+2
                load_w+=ready-start
                if first_producer is None:
                    first_producer,first_select=ready-start,select_cycles
                producer_available=ready
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
                             context=dict(stage='store_o',row_group=row_start))
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
        'memory_profile':memory_profile,
        'memory_assumptions':dict(hit_response_cycles=HS,read_miss_penalty=read_penalty,
            write_miss_penalty=write_penalty,cache_line_bytes=line_bytes,
            initially_cold=True,cpu_interference=False),
        'cache_misses':memory.misses,'dma_requests':memory.index,
        'diagnostic_dma_replay':dma_latencies is not None or dma_issue_delays is not None,
    }
    if trace:
        result.update(transactions=memory.trace,windows=window_trace,columns=column_trace)
    return result
