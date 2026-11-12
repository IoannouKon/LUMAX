package LUMAX_PACKAGE

case class LUMAXParams(
  Rin:            Int,
  Cin:            Int,
  Cout:           Int,
  XBitWidth:      Int,
  WBitWidth:      Int,
  OutBitWidth:    Int,
  minInputBits:   Int,
  minWeightBits:  Int,
  x_slice:        Int,
  y_slice:        Int,
  w_slice:        Int,
  num_filters:    Int,
  Dma_Ids:        Int,
  DMA_Bytes:      Int,
  SCALE:          Boolean,
  DEBUG:          Boolean,
  W_BUFFS:        Int,
  DMA_bits:       Int,
  Mem_row_factor : Int,
  FP16_ACTIVATIONS: Boolean = false,
)

// Elaboration-time defaults. Rebuild the design after changing these values.
object LUMAX_Config {
  val LUMAX_Config = LUMAXParams(

    // Matrix capacities for X[Rin, Cin] times W[Cin, Cout].
    Rin         = 1, // Maximum total activation rows; must be at least y_slice.
    Cin         = 1024, // Maximum K dimension; also sizes the FP16 accumulator.
    Cout        = 1024, // Maximum output columns and output-accumulator allocation.

    // Element widths and activation format.
    XBitWidth   = 16, // Activation storage lane width.
    WBitWidth   = 8, // Maximum weight width.
    OutBitWidth = 32, // Output word width.
    FP16_ACTIVATIONS = false, // Elaboration only: false=original INT, true=FP16 X / FP32 Y.

    minInputBits  = 8, // Minimum input width used to size packed-lane storage.
    minWeightBits = 2, // Minimum weight width used to size product-table storage.

    // Parallelism and table capacity.
    y_slice     =  1, // Activation rows processed in parallel.
    x_slice     =  2, // Banks per activation row and shared weight-buffer group (b).
    Mem_row_factor = 8, // Base product-table capacity factor (RF).

    // Weight-column and accumulator-bank settings.
    w_slice     = 1, // Output-column index step; the current design uses one.
    num_filters = 1, // Accumulator filter-bank allocation; the controller uses bank zero.

    // DMA request slots and beat width.
    Dma_Ids     = 2, // Request slots held until ordered response delivery.
    DMA_Bytes   = 8, // Bytes per DMA beat.
    DMA_bits    = 8*8, // Bits per DMA beat.

    // Optional output scaling.
    SCALE   = false, // Elaborate integer-output scaling when true.

    // Performance-counter instrumentation.
    DEBUG =  true, // Enable hardware performance counters.

    // Weight prefetch buffers.
    W_BUFFS =  2, // Weight buffers shared by all activation rows.

  )

}

