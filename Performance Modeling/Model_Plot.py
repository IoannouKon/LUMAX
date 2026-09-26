def plot_cycles(result, Cout, XS, YS, factor, ID, DMA_WIDTH, in_bits, w_bits):
    stages = ['Load X', 'Generate BRAMs', 'Load W & Select', 'Store Output']
    vals = [
        result['cycles_load_X'],
        result['cycles_generate_Products'],
        result['cycles_loadW_select'],
        result['cycles_store_output']
    ]
    total = result['total_cycles']
    perc = [(v / total) * 100 if total > 0 else 0 for v in vals]

    sns.set(style="whitegrid")
    plt.figure(figsize=(12, 7))
    bars = plt.bar(stages, vals, color=sns.color_palette("Set2", 4), edgecolor='black')
    plt.title(
        f'Execution cycles per stage (Total: {total:,.0f})\n'
        f'XS={XS}, YS={YS}, factor={factor}, DMA_ID={ID}, DMA_WIDTH={DMA_WIDTH}, '
        f'Act bits={in_bits}, W bits={w_bits}',
        fontsize=14
    )
    plt.ylabel('Cycles', fontsize=12)
    plt.ylim(0, max(vals) * 1.25 if vals else 1)
    for bar, v, p in zip(bars, vals, perc):
        plt.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + total * 0.01,
            f'{int(v):,}\n({p:.1f}%)',
            ha='center',
            va='bottom',
            fontsize=10,
            fontweight='bold'
        )
    plt.tight_layout()
    plt.show()


def plot_operations(result, XS, YS, factor, ID, DMA_WIDTH, in_bits, w_bits):
    """
    Plot a bar chart of the different operation counts (per activation window).
    """
    ops = ['LoadOp', 'AddOp', 'ShiftOp', 'ReadSramOp', 'WriteSramOp']
    vals = [
        result['LoadOp'],
        result['AddOp'],
        result['ShiftOp'],
        result['ReadSramOp'],
        result['WriteSramOp'],
    ]

    total_ops = sum(vals)

    sns.set(style="whitegrid")
    plt.figure(figsize=(12, 7))
    bars = plt.bar(ops, vals, color=sns.color_palette("muted", len(ops)), edgecolor='black')
    plt.title(
        f'Operation Counts per Activation Window (Total: {total_ops:,.0f})\n'
        f'XS={XS}, YS={YS}, factor={factor}, DMA_ID={ID}, DMA_WIDTH={DMA_WIDTH}, '
        f'Act bits={in_bits}, W bits={w_bits}',
        fontsize=14
    )
    plt.ylabel('Operation Count (log scale)', fontsize=12)
    plt.yscale('log')

    for bar, v in zip(bars, vals):
        if v > 0:
            plt.text(
                bar.get_x() + bar.get_width() / 2,
                v * 1.1,
                f'{int(v):,}',
                ha='center',
                va='bottom',
                fontsize=10,
                fontweight='bold'
            )

    plt.tight_layout()
    plt.show()


def plot_operations_total(result, XS, YS, factor, ID, DMA_WIDTH, Repeat, in_bits, w_bits):
    """
    Plot a bar chart of the different operation counts for the full run (total).
    """
    ops = ['LoadOp', 'WriteOp', 'AddOp', 'ShiftOp', 'ReadSramOp', 'WriteSramOp']
    vals = [
        result['LoadOp'] * Repeat,
        result['WriteOp'] * 1,
        result['AddOp'] * Repeat,
        result['ShiftOp'] * Repeat,
        result['ReadSramOp'] * Repeat,
        result['WriteSramOp'] * Repeat,
    ]

    total_ops = sum(vals)

    sns.set(style="whitegrid")
    plt.figure(figsize=(12, 7))
    bars = plt.bar(ops, vals, color=sns.color_palette("deep", len(ops)), edgecolor='black')
    plt.title(
        f'Total Operation Counts (Total: {total_ops:,.0f})\n'
        f'XS={XS}, YS={YS}, factor={factor}, DMA_ID={ID}, DMA_WIDTH={DMA_WIDTH}, '
        f'Act bits={in_bits}, W bits={w_bits}',
        fontsize=14
    )
    plt.ylabel('Operation Count (log scale)', fontsize=12)
    plt.yscale('log')

    for bar, v in zip(bars, vals):
        if v > 0:
            plt.text(
                bar.get_x() + bar.get_width() / 2,
                v * 1.1,
                f'{int(v):,}',
                ha='center',
                va='bottom',
                fontsize=10,
                fontweight='bold'
            )
    plt.tight_layout()
    plt.show()


from ipywidgets import IntSlider, interact

def interactive_model(
    Rin, Cin, Cout,
    in_bits, w_bits,
    XS, YS, factor,
    ID, DMA_WIDTH
):
    """
    Matrix multiplication: [Rin, Cin] x [Cin, Cout]

    in_bits: activation precision (8 or 16)
    w_bits:  weight precision (2, 4 or 8)

    XS:      b, πλήθος Memory Blocks dedicated to one activation vector
    YS:      how many activation vectors are handled in parallel by the accelerator
    factor:  row_factor, each memory block has 64 * row_factor rows
    ID:      DMA_ID, how many DMA requests can be sent on-the-fly
    DMA_WIDTH: number of bits transferred with one DMA request
    """
    # ---- Legend / explanation printed clearly ----
    print("Parameter meaning:")
    print(" - Rin, Cin, Cout: matrix sizes for matmul [Rin, Cin] x [Cin, Cout]")
    print(" - in_bits:  activation precision (allowed: 8 or 16)")
    print(" - w_bits:   weight precision (allowed: 2, 4 or 8)")
    print(" - XS (b):   Memory Blocks dedicated to one activation vector")
    print(" - YS:       how many activation vectors the accelerator handles in parallel")
    print(" - row_factor: each memory block has 64 * row_factor rows")
    print(" - DMA_ID:  () how many DMA requests can be in flight (ID)")
    print(" - DMA_WIDTH: bits transferred with one DMA request")
    print("-----------------------------------------------------------")

    # Fixed parameters
    buffers_w = 2
    extra_prefetch_cycles = 0
    cache_8_4 = False
    cache_reads = 1
    IN_MAX = 16

    result = compute_cycles(
        Rin, Cin, Cout, in_bits, w_bits, XS, YS, ID,
        buffers_w=buffers_w,
        factor=factor,
        DMA_WIDTH=DMA_WIDTH,
        extra_prefetch_cycles=extra_prefetch_cycles,
        cache_8_4=cache_8_4,
        cache_reads=cache_reads,
        IN_MAX=IN_MAX
    )

    Repeat = result['Repeat activation Window']

    print("\nConfiguration:")
    print(f" - MatMul: [{Rin}, {Cin}] x [{Cin}, {Cout}]")
    print(f" - in_bits (activation precision): {in_bits} bits")
    print(f" - w_bits  (weight precision):     {w_bits} bits")
    print(f" - XS (b): {XS} memory blocks / activation vector")
    print(f" - YS:     {YS} activation vectors in parallel")
    print(f" - factor (row_factor): {factor}  (rows per block = 64 * {factor})")
    print(f" - DMA_ID:    {ID} requests in flight")
    print(f" - DMA_WIDTH: {DMA_WIDTH} bits per request")

    print("\nSummary:")
    print(f" - New_Input calls (activation windows): {Repeat}")
    print(f" - Total estimated cycles: {result['total_cycles']:.0f}")
    print("\nCycles Per Stage:")
    print(f" - Load X cycles: {result['cycles_load_X']:.0f}")
    print(f" - Generate BRAMs cycles: {result['cycles_generate_Products']:.0f}")
    print(f" - Load W & Select cycles: {result['cycles_loadW_select']:.0f}")
    print(f" - Store Output cycles: {result['cycles_store_output']:.0f}")
    print(f" - Load W only (no overlap): {result['cycles load w Only']:.0f}")
    print(f" - Select only (no overlap): {result['cycles select Only']:.0f}")

    print("\nOperations Total")
    print(f" - LoadOp: {result['LoadOp'] * Repeat}")
    print(f" - WriteOp: {result['WriteOp'] * 1}")
    print(f" - AddOp: {result['AddOp'] * Repeat}")
    print(f" - ShiftOp: {result['ShiftOp'] * Repeat}")
    print(f" - ReadSramOp: {result['ReadSramOp'] * Repeat}")
    print(f" - WriteSramOp: {result['WriteSramOp'] * Repeat}")

    print("\nOperations Per Activation Window")
    print(f" - LoadOp: {result['LoadOp']}")
    print(f" - AddOp: {result['AddOp']}")
    print(f" - ShiftOp: {result['ShiftOp']}")
    print(f" - ReadSramOp: {result['ReadSramOp']}")
    print(f" - WriteSramOp: {result['WriteSramOp']}")

    # Plots (unchanged)
    plot_cycles(result, Cout, XS, YS, factor, ID, DMA_WIDTH, in_bits, w_bits)
    plot_operations(result, XS, YS, factor, ID, DMA_WIDTH, in_bits, w_bits)
    plot_operations_total(result, XS, YS, factor, ID, DMA_WIDTH, Repeat, in_bits, w_bits)


# Sliders with short, non‑truncated labels
interact(
    interactive_model,
    # Matrix Dimensions [Rin, Cin] x [Cin, Cout]
    Rin=IntSlider(min=1, max=32000, step=1, value=21, description='Rin'),
    Cin=IntSlider(min=32, max=32000, step=32, value=100, description='Cin'),
    Cout=IntSlider(min=32, max=32000, step=32, value=100, description='Cout'),

    # Matrix precisions
    in_bits=IntSlider(min=8, max=16, step=8, value=16, description='in_bits'),
    # w_bits=IntSlider(min=2, max=8, step=2, value=8, description='w_bits'),
    w_bits = SelectionSlider(
    options=[2, 4, 8],
    value=8,
    description='w_bits',
    disabled=False,
    continuous_update=False,
    orientation='horizontal',
    readout=True
    ),

    # HW / Memory Parameters
    XS=IntSlider(min=1, max=32, step=1, value=4, description='XS'),
    YS=IntSlider(min=1, max=1, step=1, value=1, description='YS'),
    factor=IntSlider(min=1, max=64, step=1, value=16, description='row factor'),

    # DMA Parameters
    ID=IntSlider(min=1, max=64, step=1, value=4, description='DMA_ID'),
    DMA_WIDTH=IntSlider(min=64, max=128, step=64, value=64, description='DMA_W')
)