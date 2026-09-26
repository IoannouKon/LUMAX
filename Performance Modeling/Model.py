from itertools import repeat
import math
import matplotlib.pyplot as plt
from ipywidgets import interact, IntSlider, Checkbox
import seaborn as sns
from ipywidgets import SelectionSlider


def dma_finish_time_exact_bits(total_bits, ID, t_issue=1, L=6, DMA_WIDTH_bits=64, beat_time=1):
    """
    Exact finish time (cycles) to transfer total_bits when each packet = DMA_WIDTH_bits.
    - total_bits : total data size in bits
    - ID         : max outstanding requests (DMA_ID)
    - t_issue    : cycles to post one request (Tx), default 1
    - L          : round-trip latency from issue to first returned beat (HS)
    - DMA_WIDTH_bits : width in bits of one DMA packet/beat
    - beat_time  : cycles per returned beat (default 1)
    Returns finish_time in cycles (int).
    """
    if total_bits <= 0:
        return 0
    P = int(math.ceil(float(total_bits) / float(DMA_WIDTH_bits)))  # number of packets
    if P <= 0:
        return 0

    issue_time = [0.0] * P
    completion_time = [0.0] * P

    issue_time[0] = 0.0
    completion_time[0] = issue_time[0] + L  # beats_per_packet = 1 -> no extra beat delay here

    for k in range(1, P):
        earliest_issue = issue_time[k-1] + t_issue
        if k >= ID:
            earliest_issue = max(earliest_issue, completion_time[k - ID])
        issue_time[k] = earliest_issue
        completion_time[k] = issue_time[k] + L

    finish = max(completion_time)
    return int(math.ceil(finish))


def compute_cycles(
    Rin, Cin, Cout, in_bits, w_bits, XS, YS, ID,
    buffers_w=2, factor=4,
    DMA_WIDTH=64, extra_prefetch_cycles=0,
    cache_8_4=False, cache_reads=1,
    IN_MAX=16,
    IN_min=8, W_MAX=8, HS=8
):
    """
    Compute cycle and operations model for the accelerator.
    """
    HS = 8  # Handshake Protocol estimation according to verilator

    # Operations Counter (PER ACTIVATION WINDOW)
    LoadOp = 0   # Load from memory via DMA
    WriteOp = 0  # Write to memory via DMA
    AddOp = 0    # add "+" partial products
    ShiftOp = 0  # shift activations or weights
    ReadSramOp = 0  # Read from Sync Mem
    WriteSramOp = 0 # Write to Sync Mem

    # Scaling factor for activations
    factor_X = IN_MAX / in_bits

    def round_up_div(a, b):
        return (a + b - 1) // b

    # ----------------------------------------------------
    # Stage 1 - Load X (Load Activations)
    # ----------------------------------------------------

    # Cycle Modeling
    activation_block_default = (2 ** (W_MAX - w_bits))  # The maximum amount of activation blocks a memory can hold with no factors
    activation_window_per_Mem = activation_block_default * factor_X * factor # The maximum amount of activation blocks a memory can hold with factors
    activation_window_XS = min(XS * activation_window_per_Mem, Cin)
    activation_window = activation_window_XS * min(YS, Rin)

    activation_data = activation_window * in_bits
    packets_in = math.ceil(activation_data / DMA_WIDTH)
    real_ID = min(ID, packets_in)

    # compute exact DMA cycles (one packet == DMA_WIDTH bits)
    cycles_1 = dma_finish_time_exact_bits(
        total_bits=int(activation_data),
        ID=ID,
        t_issue=1,
        L=HS,
        DMA_WIDTH_bits=DMA_WIDTH,
        beat_time=1
    )

    Repeat = round_up_div(Cin, activation_window_XS) * round_up_div(Rin, min(YS, Rin))  # How many activation windows needed to complete mat-mul
    cycles_1_Total = cycles_1 * Repeat

    # Ops per activation window
    LoadOp += packets_in
    WriteSramOp += packets_in

    # ----------------------------------------------------
    # Stage 2 - Generate Products
    # ----------------------------------------------------

    # activation_per_Mem = math.ceil(min(XS * activation_window_per_Mem, Cin)  / XS)
    activation_per_Mem = min(activation_window_per_Mem,math.ceil(Cin / XS))

    #for debug
    total_padding = activation_per_Mem * XS - min(XS * activation_window_per_Mem, Cin)
    dummy_per_mem_base = total_padding // XS


    memory_blocks = XS * min(YS, Rin)
    active_activations_blocks = (activation_per_Mem / factor_X)

    cycles_2 = ((2 ** (w_bits - 2)) * active_activations_blocks + active_activations_blocks)  # Cycles needed to generate all activation products for one activation window
    cycles_2_Total = cycles_2 * Repeat

    # Ops per activation window
    ReadSramOp += (cycles_2 * memory_blocks)
    WriteSramOp += (cycles_2 * memory_blocks)
    AddOp += ((cycles_2 - 1) * memory_blocks)
    ShiftOp += activation_window  # one shift for every activation(I) to produce (2*I)

    # ----------------------------------------------------
    # Stage 3 - Load W & Select (producer–consumer model)
    # ----------------------------------------------------
    weight_elems = activation_window  # Need to load for every activation one weight from every weight matrix column
    packets_w = math.ceil((weight_elems * w_bits) / DMA_WIDTH)
    real_ID = min(ID, packets_w)

    cycles_3_1 = dma_finish_time_exact_bits(
        total_bits=int(weight_elems * w_bits),
        ID=ID,
        t_issue=1,
        L=HS,
        DMA_WIDTH_bits=DMA_WIDTH,
        beat_time=1
    )
    cycles_3_2 = 1 + (activation_per_Mem) + 2  # select & accumulate per window per column

    cycles_3_one_window = (Cout - 2) * max(cycles_3_1, cycles_3_2)
    cycles_3_Total = cycles_3_one_window * Repeat

    # Ops per activation window
    LoadOp += packets_w
    WriteSramOp += packets_w
    ReadSramOp += (cycles_3_2 * memory_blocks)
    AddOp += (cycles_3_2 * memory_blocks)
    ShiftOp += (cycles_3_2 * memory_blocks)

    # ----------------------------------------------------
    # Stage 4 - Store Output
    # ----------------------------------------------------
    output_elems = Cout * min(YS, Rin)
    O_bits = 32
    packets_0 = math.ceil((output_elems * O_bits) / DMA_WIDTH)
    real_ID = min(ID, packets_0)
    cycles_4 = dma_finish_time_exact_bits(
        total_bits=int(output_elems * O_bits),
        ID=ID,
        t_issue=1,
        L=HS,
        DMA_WIDTH_bits=DMA_WIDTH,
        beat_time=1
    )
    cycles_4_Total = cycles_4 * round_up_div(Rin, YS)

    # Ops per activation window (output write)
    WriteOp += packets_0 * round_up_div(Rin, YS)

    # ----------------------------------------------------
    # Total cycles
    # ----------------------------------------------------
    total_cycles = cycles_1_Total + cycles_2_Total + cycles_3_Total + cycles_4_Total

    return {
        'Repeat activation Window': Repeat,
        'cycles_load_X': cycles_1_Total,
        'cycles_generate_Products': cycles_2_Total,
        'cycles_loadW_select': cycles_3_Total,
        'cycles_store_output': cycles_4_Total,
        'total_cycles': total_cycles,
        'cycles load w Only': cycles_3_1 * Repeat * Cout,
        'cycles select Only': cycles_3_2 * Repeat * Cout,
        'cycles_3_1': cycles_3_1,
        'cycles_3_2': cycles_3_2,
        # Ops PER activation window:
        'LoadOp': LoadOp,
        'WriteOp': WriteOp,
        'AddOp': AddOp,
        'ShiftOp': ShiftOp,
        'ReadSramOp': ReadSramOp,
        'WriteSramOp': WriteSramOp,
    }