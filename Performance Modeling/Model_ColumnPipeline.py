"""Experimental column-pipeline model compared with controller-events-v3.

This is a scheduling what-if, not an implemented or Verilator-verified design.
It retains the same b/RF/YS datapath, five-cycle selection latency and memory
interface, but permits tagged columns to overlap pipeline fill/drain. Activation
and weight addresses must be able to switch columns without extra read bubbles.
The frozen archive/Model_v3.py supplies the reference and shared helpers.
Production Model.py now models the accepted four-cycle tagged RTL separately.
"""
from pathlib import Path
import hashlib
import importlib.util

MODEL_VERSION = 'column-pipeline-experimental-v1'
_BASE_PATH = Path(__file__).parent / 'archive' / 'Model_v3.py'
V3_MODEL_SHA256 = hashlib.sha256(_BASE_PATH.read_bytes()).hexdigest()
_SPEC = importlib.util.spec_from_file_location('_lumax_column_reference', _BASE_PATH)
_BASE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BASE)
if _BASE.MODEL_VERSION != 'controller-events-v3':
    raise RuntimeError('This experiment requires archived controller-events-v3')
_integer = _BASE._integer
_read_packets = _BASE._read_packets
_store_packets = _BASE._store_packets
_Memory = _BASE._Memory


def compute_cycles(Rin, Cin, Cout, in_bits, w_bits, XS, YS, ID,
                   buffers_w=2, factor=4, DMA_WIDTH=64,
                   extra_prefetch_cycles=0, cache_8_4=False, cache_reads=1,
                   IN_MAX=16, IN_min=8, W_MAX=8, HS=7,
                   activation='int16', memory_profile='chipyard_llc',
                   cache_line_bytes=64, cache_bytes=512*1024,
                   read_miss_penalty=11, write_miss_penalty=4,
                   x_address=0x10000000, w_address=0x20000000,
                   o_address=0x30000000, block_size=None, trace=False,
                   dma_latencies=None, dma_issue_delays=None, column_gap=0):
    """Predict hypothetical tagged-column cycles; not the implemented RTL.

    column_gap=0 streams columns; column_gap=5 reproduces v3 for validation.
    The five-cycle latency remains, including the final drain of each K window.
    No CPU, printing, wall-clock or startup cycles are included.

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
    column_gap=_integer('column_gap',column_gap,0)
    if column_gap>5:
        raise ValueError('column_gap must be between 0 and 5')
    names=('Rin','Cin','Cout','XS','YS','ID','buffers_w','factor','DMA_WIDTH')
    vals=dict(zip(names,(Rin,Cin,Cout,XS,YS,ID,buffers_w,factor,DMA_WIDTH)))
    Rin,Cin,Cout,XS,YS,ID,buffers_w,factor,DMA_WIDTH=(_integer(k,vals[k]) for k in names)
    if in_bits!=16 or IN_MAX!=16 or W_MAX!=8 or w_bits not in (2,4,8):
        raise ValueError('Verified model supports input16 and W8/W4/W2 (IN_MAX=16, W_MAX=8)')
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
            finished_columns=[]
            last_retire=select_available
            select_cycles=bank_span+5
            for col in range(Cout):
                start=max(producer_available,finished_columns[col-buffers_w] if col>=buffers_w else gen_start)
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
                retire_end=read_end+5
                # A tagged pipeline allows the next column to read while the
                # preceding column's products are still reaching the accumulator.
                # Release its weight buffer after the last captured read, plus
                # any deliberately retained handoff gap. No extra read ports.
                select_available=read_end+column_gap
                finished_columns.append(select_available)
                # Occupied selection time is the union of overlapping pipeline
                # intervals; summing every column's latency would double-count.
                selection+=retire_end-max(begin_select,last_retire)
                if column_trace is not None:
                    column_trace.append(dict(row_group=row_start,k_start=k_start,
                        column=col,weight_ready=ready,issue_start=begin_select,
                        read_end=read_end,buffer_release=select_available,
                        retire=retire_end))
                last_retire=retire_end
            # w_idx reaches Cout one edge before leaving the combined state.
            now=last_retire+1
            combined+=now-(gen_start+gen_cycles)
            if window_trace is not None:
                window_trace.append(dict(row_group=row_start,k_start=k_start,k=length,
                    bank_span=bank_span,generation=gen_cycles,
                    loadw_select=now-(gen_start+gen_cycles),select_per_column=select_cycles,
                    column_issue_interval=bank_span+column_gap))
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
        'experimental':True,'reference_model':_BASE.MODEL_VERSION,
        'column_gap':column_gap,'pipeline_latency':5,
        'assumptions':'Tagged columns; early weight-buffer release; one bank step per cycle; unchanged memory ports and clock.',
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
