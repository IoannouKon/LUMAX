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
)

// Create an object to hold your configuration
object LUMAX_Config {
  val LUMAX_Config = LUMAXParams(

    //Μax Matrix Dimesnions 
    Rin         = 1,  //Used Only for a Counter  (SAME for YS for now)
    Cin         = 800, // Used only for OuputBitwidth 
    Cout        = 800, // Used only to a a counter Registers  and how many output stationary outputs we have 
  
    // Element Bidwidths  
    XBitWidth   = 16,  // max Number of bits on input elemetns 
    WBitWidth   = 8,   // max  Number of bits on weights elemetns 
    OutBitWidth = 32,  // max  Number of bits on output elements  
    //params.XBitWidth + params.WBitWidth + Math.ceil(math.log(params.Cin) / math.log(2)).toInt

    //new
    minInputBits  = 8,  //2 4
    minWeightBits = 2,  // 4 
    
    //Max Parallelism Paramters 
    y_slice     =  1,    // IMPORTANT Design parameter  MAX value
    x_slice     =  2,   // IMPORTANT Design parameter  MAX value 
    Mem_row_factor = 4,
    
    //NOT USED (TODO)
    w_slice     = 1,
    num_filters = 1,

    //DMA 
    Dma_Ids     = 2,  // How many DMA Requests in flight 
    DMA_Bytes   = 8,  // How many bytes send in one request 
    DMA_bits    = 8*8,

    //scale 
    SCALE   = false, //1 or 0  (elaboration-time)

    //Debug from performance counters 
    DEBUG =  true, // 1 or  0

    // How many Load Buffer 
    W_BUFFS =  2,

  )

}

