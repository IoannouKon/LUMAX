///////////////////////////////////////////////////////////////////////////////////////////////////////////////
package Data_Reuse

import chisel3._
import chisel3.util._

import freechips.rocketchip.tile._
import org.chipsalliance.cde.config._
import freechips.rocketchip.diplomacy._
import freechips.rocketchip.tile.{BuildRoCC, OpcodeSet}
import freechips.rocketchip.diplomacy.LazyModule
import os.truncate
import firrtl.PrimOps.Add
import scala.annotation.meta.param
import freechips.rocketchip.rocket._
import freechips.rocketchip.tilelink.TLMessages.c
import freechips.rocketchip.rocket.{TLBConfig, HellaCacheReq} 
import org.chipsalliance.cde.config.Parameters
import freechips.rocketchip.amba.axi4._
import freechips.rocketchip.diplomacy._
import freechips.rocketchip.subsystem.{CacheBlockBytes, CrossesToOnlyOneClockDomain}
import freechips.rocketchip.tilelink.{TLBuffer, TLInwardNode, TLToAXI4}
import chisel3.experimental._

//my imports
import Data_Reuse.ChunkUtils._   // import your function from the object
import Data_Reuse.Data_Reuse_Config


class DataReuseExample(opcodes: OpcodeSet, val params: DataReuseParams)
  (implicit p: Parameters) extends LazyRoCC(opcodes = opcodes) {

  override lazy val module = new DataReuseExampleModuleImpl(this)  

  val dma = LazyModule(new DmaModule(Data_Reuse_Config.Data_Reuse_Config))
  //val buffer  = 

  // tlNode  := TLBuffer(
  // BufferParams.default,
  // BufferParams.none,
  // BufferParams.none,
  // BufferParams.default,
  // BufferParams.none) := dma.node  // Connect the memory module to the accelerator’s TileLink node

  tlNode   := dma.node  // Connect the memory module to the accelerator’s TileLink node

}

class DataReuseExampleModuleImpl(outer: DataReuseExample)(implicit p: Parameters) extends LazyRoCCModuleImp(outer) with HasCoreParameters {

  val params: DataReuseParams = outer.params
  val dma = outer.dma.module

  // ROCC interface
  val cmd = Queue(io.cmd)
  val funct = cmd.bits.inst.funct
  val rs1 = cmd.bits.rs1 //address
  val rs2 = cmd.bits.rs2 //values 


  //Custom Instructions 
  val mode_0         = funct === 0.U  // load X address and x_slice dimesnsion 
  val mode_1         = funct === 1.U  // load w address and Cout dimension 
  val mode_2         = funct === 2.U  // load O address and  Cin/x_slice value 
  val mode_3         = funct === 3.U  // load parallel Rin dimesnion and Cin dimension 
  val mode_4         = funct === 4.U  // select data bit width of input matrices   
  val mode_5         = funct === 5.U  // load how many loops are needed  
  val mode_6         = funct === 6.U  // stop busy signal   
  val mode_7         = funct === 7.U  // load scale factor 
  val mode_8         = funct === 8.U  // load elements precission  
  val mode_9         = funct === 9.U  // Performance counters 
  val mode_10        = funct === 10.U // Start Calcukation  

  val row_factor = params.Row_factor   
  val width_factor = 1 //DONT CHANGE 

  //DMA Module inputs 
  dma.io.valid     := false.B
  dma.io.addr      := 0.U
  dma.io.mode      := 3.U      // inactive  
  dma.io.writeData := 0.U
  dma.io.log_S     := log2Ceil(params.DMA_Bytes).U
  dma.io.mask      := 0.U   
  dma.io.d_ready   := false.B //not used 

  //SyncMem compile parameters  
  val RowsPerBlock      = (( 1 << (params.WBitWidth - 1) ) / 2) * row_factor * params.Mem_row_factor  // How many Rows have one SynMem 
  val totalSyncMems     =  (params.x_slice * params.y_slice) / (row_factor )     //How  many SyncMem the design need to worst case 

  val maxParts         = params.XBitWidth / params.minInputBits   // at Worst case one X_reg / SynMem have splitted have maxPart differentt elemtns 
  val product_bitwidth = (params.XBitWidth/params.minInputBits)*(params.WBitWidth + params.minInputBits) * width_factor

  val Products = RegInit(VecInit(Seq.fill(totalSyncMems)(0.U(product_bitwidth.W)))) // Store read results

  val done          = RegInit(false.B)
  val Done          = RegInit(false.B)
  val Done_acc      = RegInit(false.B)
  val delay         = RegInit(false.B)
  val delay_counter = RegInit(0.U(3.W))  // Enough to count up to 2

  // val sum    = RegInit(VecInit(Seq.fill(totalSyncMems)(0.S(params.XBitWidth.W))))  // sum in Select Products and Accumulation
  val sum    = Reg(Vec(totalSyncMems, SInt(params.XBitWidth.W)))  // sum in Select Products and Accumulation
  val DATA_Y = Reg(Vec(totalSyncMems, UInt(product_bitwidth.W)))                   // Store read results
  val Sign   = Reg(Vec(totalSyncMems, Bool()))                                     // Store the sign as a boolean (true/false) for each BRAM 


  //How many X_reg will neeed in worst case 
  val regs_per_mem = (1 << (params.WBitWidth - params.minWeightBits)) * width_factor  * params.Mem_row_factor
  val regs_per_mem_w = (params.XBitWidth/params.minInputBits) * regs_per_mem 
  val max_x_regs  = params.x_slice * regs_per_mem //same as total blocks (one X_reg at worst case for every SynMem)
  val max_w_elems = (params.XBitWidth/params.minInputBits) * max_x_regs              // How many W registers we will neeed in worst case to bring all corresping elemetns (speedup)
  val buffers     =  params.W_BUFFS 

  // Create Scratchpad Memory (Registers)  
  val O_reg     = RegInit(VecInit(Seq.fill(params.num_filters)(VecInit(Seq.fill(params.y_slice)(VecInit(Seq.fill(params.Cout)(0.S(params.OutBitWidth.W))))))))  // Register for Output results
  // val Acc_reg = Reg(Vec(params.y_slice, Int(params.OutBitWidth.W)))
  // val X_reg = Reg(Vec(params.y_slice, Vec(max_x_regs, SInt(params.XBitWidth.W))))  // Register for each row of X 
  // val W_reg = Reg(Vec(params.num_filters, Vec(max_w_elems, Vec(buffers, SInt(params.WBitWidth.W)))) )  // Register for current column of W for every filter 

  // ------------- Sync Read Memory System ------------------ // 


  // ----------------------- Activations Banks ------------------------------ //
  // [0,blocks_in_Sync_mem-1] totalSyncMems =0 |  [blocks_in_Sync_mem,2*blocks_in_Sync_mem-1] totalSyncMems = 1 .... 
  
  // val X_MEM = Seq.fill(totalSyncMems) {SyncReadMem(regs_per_mem, SInt(params.XBitWidth.W))} 
   val X_MEM = Seq.fill(totalSyncMems)(SyncReadMem(regs_per_mem/4, UInt(params.DMA_bits.W)))
  // val X_debug = RegInit(VecInit(Seq.fill(totalSyncMems)(0.U(64.W)))) // Store read results


  //NEW logic 
  val max_blocks_in_sync_mem = (1 << (params.WBitWidth - params.minWeightBits)) * row_factor

  // counter register to iterate rows
  val counter_row = RegInit(0.U(log2Ceil(max_blocks_in_sync_mem).W))
  val counter_reg = RegInit(0.U(log2Ceil(max_x_regs + 1).W))
  val counter_mem = RegInit(0.U(log2Ceil(totalSyncMems).W))
  val offset_counter = RegInit(0.U(log2Ceil(4).W))

  val X_vals = RegInit(VecInit(Seq.fill(totalSyncMems)(0.U(64.W)))) // Store read results

   ///debug 
    val initDone = RegInit(false.B)
    val initAddr = RegInit(0.U(log2Ceil(regs_per_mem/4).W))

    when (!initDone) {
      for (i <- 0 until totalSyncMems) {
        X_MEM(i).write(initAddr, 0.U)
      }
      initAddr := initAddr + 1.U
      when (initAddr === (regs_per_mem/4-1).U) {
        initDone := true.B
      }
    }
   //debug 
   
  // ----------------------- Weight Banks ------------------------------ //
   val W_MEM = Seq.fill(buffers, totalSyncMems) { SyncReadMem(regs_per_mem_w/8, UInt(64.W))}


    // counter register to iterate rows
  val counter_row_w = RegInit(0.U(log2Ceil(max_blocks_in_sync_mem).W))
  val counter_reg_w = RegInit(0.U(log2Ceil(max_w_elems + 1).W))
  val counter_mem_w = RegInit(0.U(log2Ceil(totalSyncMems).W))
  val valid_inputs  = Reg(UInt((totalSyncMems*64/params.XBitWidth).W))
  val valid_weights = Reg(Vec(buffers, UInt((totalSyncMems*64/params.WBitWidth).W)))


  // val W_vals = RegInit(VecInit(Seq.fill(totalSyncMems)(0.U(64.W)))) // Store read results
  // val W_debug = RegInit(VecInit(Seq.fill(totalSyncMems)(0.U(64.W)))) // Store read results
  // val W_debug_2 = RegInit(VecInit(Seq.fill(totalSyncMems)(0.U(64.W)))) // Store read results


  val w_elems_per_chunk = Reg(UInt(6.W)) // 6-bit register, no explicit init

// BANK          BANK 0                 |        BANK 1                                   |  ... |     BANK n
// row 0     [0-(w_elems_per_chunk-1)]   |  [w_elems_per_chunk - (2*w_elems_per_chunk -1)] |  ... |     [(n-1)*w_elems_per_chunk - (n*w_elems_per_chunk -1)]
 
// ----------------------------------- weight BANKS (totalSyncMems BANKS)  adn w_elems_per_chunk = (8, 16, 32) dynamically  
//  ------- BANK i και ROW j --------
//  startIndex = j * (totalSyncMems * w_elems_per_chunk) + i * w_elems_per_chunk
//  endIndex   = startIndex + w_elems_per_chunk - 1
//  BANK i, ROW j → [ startIndex ... endIndex ]
// ----------------------------------- 


// ----------------------------------- activations BANKS (totalSyncMems BANKS)
//  ------- BANK i και ROW j --------
//  startIndex = j * (totalSyncMems * 4) + i * 4
//  endIndex   = startIndex + 4 - 1
//  BANK i, ROW j → [ startIndex ... endIndex ]
// ----------------------------------- 

//note 
// ✅ Άρα στην περίπτωσή σου (με w_elems_per_chunk = 8,16,32) δεν υπάρχει περίπτωση δύο activations στην ίδια row να ζητάνε weights από διαφορετικές rows.

val row_weight = RegInit(0.U(log2Ceil(max_blocks_in_sync_mem).W))
val offset_counter_w = RegInit(0.U(log2Ceil(8).W))
val elements_counter = RegInit(0.U(log2Ceil(totalSyncMems * 32).W))

  //  /debug 
    val initDoneW = RegInit(false.B)
    val initAddrW = RegInit(0.U(log2Ceil(regs_per_mem_w/8 ).W))
    when (!initDoneW) {
      for (b <- 0 until buffers) {
        for (i <- 0 until totalSyncMems) {
          W_MEM(b)(i).write(initAddrW, 0.U)
        }
      }
      initAddrW := initAddrW + 1.U
      when (initAddrW === (regs_per_mem_w/8 - 1).U) {
        initDoneW := true.B
      }
    }
    //  /debug 

  
  // ------------- Sync Read Memory System ------------------ // 

// //  if(params.SimpleCache4x8){ 
//     //Cache_4_8 Register mode 
//     val Cache_4_8 = RegInit(VecInit(
//       (1 to 128).map { w =>
//         VecInit(Seq(2, 4, 6, 8).map { x =>
//           (w * x).U(12.W)
//         })
//       }
//     ))
// //  }  

 val start_base = RegInit(0.U(log2Ceil(max_w_elems.toInt + 1).W))

  //Double Bufferig Parameters 
  val buffers_mode  = RegInit(VecInit(Seq.fill(buffers)(false.B)))  // 0-> load W ,  1 -> SelectandAccumulate 
  val buff_elems    = RegInit(VecInit(Seq.fill(buffers)(0.U(log2Ceil(math.max(max_x_regs, params.Cout) + 1).W))))
  val dirty_elems_w = RegInit(VecInit(Seq.fill(buffers + 1)(0.U(log2Ceil(params.DMA_bits / params.minWeightBits).W))))
  val dirty_elems_w_counter_1  = RegInit(0.U(log2Ceil(buffers+1).W))
  val dirty_elems_w_counter_2  = RegInit(0.U(log2Ceil(buffers+1).W))


  val load_w_buff_idx  = RegInit(0.U(log2Ceil(buffers).W))
  val select_buff_idx  = RegInit(0.U(log2Ceil(buffers).W))
  val w_count          = RegInit(0.U(log2Ceil(params.Cout + 1).W)) // counter to for current Weight matrix Column 

  //Create BRAMS 
  val temp_muls_mem = Seq.fill(totalSyncMems) {SyncReadMem(RowsPerBlock  , UInt(product_bitwidth.W))} //FIX THIS
  val cycleCount_1    = RegInit(0.U(log2Ceil(64 + 1).W)) // Track the current block to write //blocksPerBRAM 
  val cycleCount_2    = RegInit(0.U(log2Ceil(64 + 1).W)) // Track the current block to write //blocksPerBRAM 
  val cycleCount_2_reg    = RegInit(0.U(log2Ceil(64 + 1).W)) // Track the current block to write //blocksPerBRAM

  // Index counters 
  val w_idx         = RegInit(0.U(log2Ceil(params.Cout + 1).W)) // counter to for current Weight matrix Column 
  val f_idx         = RegInit(0.U(log2Ceil(params.num_filters  + 1).W)) //counter to track current Weight fildter (NOT USED)   
  val load_x_cnt    = RegInit(0.U(64.W)) // track the cuurenct x_slice have been loaded [1,Cin/x_slice]
  val Rin_cnt       = RegInit(0.U(log2Ceil(params.y_slice + 1).W)) //track the current Rin that processed [1,parallel_Rin]
  val loops_cnt     = RegInit(0.U(64.W)) 

  //Indexer for Matrices 
  val i_w           = RegInit(0.U(log2Ceil(params.Cin + 1).W)) 
  val j_w           = RegInit(0.U(log2Ceil(params.Cout + 1).W)) 
  val i_x           = RegInit(0.U(log2Ceil(params.Cin + 1).W)) 
  val j_x           = RegInit(0.U(log2Ceil(params.Cin + 1).W)) 
  val i_w_start     = RegInit(0.U((log2Ceil(params.Cin + 1).W))) 
  val i_x_start     = RegInit(0.U(log2Ceil(params.Cin + 1).W))  
  val j_x_start     = RegInit(0.U(log2Ceil(params.Cout + 1).W)) 
  val LOOPS         = RegInit(0.U(64.W)) 
  val i_o           = RegInit(0.U(log2Ceil(params.Rin + 1).W)) 
  val j_o           = RegInit(0.U(log2Ceil(params.Cout + 1).W)) 
  val j_x_temp      = RegInit(0.U(log2Ceil(params.Cin + 1).W)) 
  val i_w_temp      = RegInit(0.U(log2Ceil(params.Cin + 1).W)) 

  //new -- dirty w 
  // val i_w_d           = RegInit(0.U(log2Ceil(params.Cin + 1).W)) 
  // val j_w_d           = RegInit(0.U(log2Ceil(params.Cout + 1).W)) 
  val init_cycle      = RegInit(true.B)
  val step_new        = RegInit(0.U(log2Ceil(params.Cin + 1).W)) 

 // Register to keep loops limit at runtime 
  val XS            =  RegInit(max_x_regs.U(log2Ceil(max_x_regs + 1).W))
  val XS_W          =  RegInit(max_w_elems.U(log2Ceil(max_w_elems + 1).W))
  val YS            =  RegInit(params.y_slice.U(log2Ceil(params.y_slice + 1).W))
  val XS_b          =  RegInit(max_x_regs.U(log2Ceil(max_x_regs + 1).W))
  val YS_b          =  RegInit(params.y_slice.U(log2Ceil(params.y_slice + 1).W))

  val elems_per_chunk        = (64/params.OutBitWidth)
  val done_elems_in_block    =  RegInit(0.U(log2Ceil(elems_per_chunk).W))

  //Produce register files for method -2 only 
  val counter_1 = RegInit(0.U(log2Ceil(RowsPerBlock + 2).W))  // Counter to track how many Register Files entries are filled
  val counter_2 = RegInit(0.U(log2Ceil(RowsPerBlock + 2).W))  // Counter to track how many Register Files entries are filled

  //Add registers to track the ongoing DMA transaction's mode
  val dma_send        = RegInit(false.B) //sending DMA requests to A channel on progress
  val dma_resp        = RegInit(false.B) //receiveving  DMA responces from  D channel on progress
  val stage_1         = RegInit(false.B)
  val stage_1_w         = RegInit(false.B)

  val dma_counter_a   = RegInit(0.U(log2Ceil(math.max(max_x_regs, params.Cout) + 1).W))  
  val dma_counter_d   = RegInit(0.U(log2Ceil(math.max(max_x_regs, params.Cout) + 1).W)) 
  
  //Select and Accumulte control Signals 
  val lock            = RegInit(false.B) //send one DMA request to A channel and wait the responce from D channel (muitex)  
  val READ_ON         = RegInit(false.B) 
  val read_on_delay   = RegInit(false.B)
  // read_on_delay := (RegNext(READ_ON))
  // val read_on_delay   = RegNext(RegNext(READ_ON))

  
  // Matrices Addresses
  val adr_X      =  RegInit(0.U(64.W)) 
  val adr_W      =  RegInit(0.U(64.W)) 
  val adr_O      =  RegInit(0.U(64.W)) 

  // // DEBUG ONLY 
  // val X_DATA      =  RegInit(0.U(64.W)) 
  // val W_DATA      =  RegInit(0.U(64.W)) 
  
  //Register to save dimension values 
  val dma_limit             = RegInit(0.U(log2Ceil(params.Cin + 1).W)) //Cin/X_slice
  val cout_reg              = RegInit(params.Cout.U(log2Ceil(params.Cout + 1).W))
  val cin_reg               = RegInit(params.Cout.U(log2Ceil(params.Cin + 1).W))
  val rin_reg               = RegInit(params.Rin.U(log2Ceil(params.Rin + 1).W))
  val x_slice_Reg           = RegInit(params.x_slice.U(log2Ceil(params.x_slice + 1).W))
  val y_slice_Reg           = RegInit(params.y_slice.U(log2Ceil(params.y_slice + 1).W))
  val x_slice_weights_reg   = RegInit(max_w_elems.U(log2Ceil(max_w_elems + 1).W)) // to bring all corresping weights for all x_elemnts  
  val x_slice_input_reg     = RegInit(max_x_regs.U(log2Ceil(max_x_regs + 1).W)) // so in worst case every sync Mem have hiw own X_reg 


  // if(params.SCALE){ //SCALE
    val scale = RegInit(0.U(32.W)) // 32-bit register for IEEE 754 float
  // }  

  //bitwidths 
  val input_bits  = RegInit(0.U(6.W)) //  input bits 
  val weight_bits = RegInit(0.U(6.W)) //  weights bits 
  val output_bits = RegInit(0.U(6.W)) //  output bits 

  w_elems_per_chunk := 64.U / weight_bits 


  // if(params.DEBUG){
    //Performace counters per state
    val PC_loadX    =  RegInit(0.U(64.W)) 
    val PC_Generate =  RegInit(0.U(64.W)) 
    val PC_loadW    =  RegInit(0.U(64.W)) 
    val PC_Select   =  RegInit(0.U(64.W)) 
    val PC_storeO   =  RegInit(0.U(64.W)) 
    val PC_loadW_and_Select = RegInit(0.U(64.W)) 
  // }

  //Dynamic Input Paramters  characteristcs for every w element 
  val all_available_elems   = RegInit(0.U(log2Ceil(params.Cin + 1).W))                        //new 
  val elemOffVec            = Reg(Vec(totalSyncMems, UInt(log2Ceil(maxParts).W)))             // Keep in what offset ot row is located the element weight want to read 
  val w_reg_valid           = Reg(Vec(totalSyncMems, Bool()))                                 // true -> selected value from BRAM is valid   
  val dirty_elems_x         = RegInit(0.U(log2Ceil(params.DMA_bits/params.minInputBits).W))  //new 
  val all_available_elems_x = RegInit(0.U(log2Ceil(params.Cout + 1).W))                       //new 
  // val w_elems_per_reg       = RegInit((params.WBitWidth / params.minWeightBits).U)            //How many weight elements fit in one Weight register 
  val x_elems_per_reg       = RegInit((params.XBitWidth / params.minInputBits).U)             //How many input elements fit  in one input register 

  val readDataVec_part            = Reg(Vec(totalSyncMems, UInt(log2Ceil(maxParts).W)))             // Keep in what offset ot row is located the element weight want to read 


  val w_elems_per_reg = RegInit(
  (params.WBitWidth / params.minWeightBits).U(3.W) // 3-bit wide register
)


  //Dynamic Bitwidth 
    val mask_in              = RegInit(0.U(log2Ceil(((1 << params.XBitWidth) - 1) + 1).W))
    val mask_w               = RegInit(0.U(log2Ceil(((1 << params.WBitWidth) - 1) + 1).W))  
    val mask_p               = RegInit(0.U(log2Ceil(((1 << (params.WBitWidth + params.XBitWidth)) - 1) + 1).W))   
    val block_rows_1         = RegInit(0.U(log2Ceil((1 << (params.WBitWidth - 2)) + 1).W))
    val block_rows_2         = RegInit(0.U(log2Ceil((1 << (params.WBitWidth - 2)) + 1).W))
    val blocks_in_Sync_mem_1 = RegInit(0.U(log2Ceil((1 << (params.WBitWidth - params.minWeightBits) * row_factor) + 1).W))
    val blocks_in_Sync_mem_2 = RegInit(0.U(log2Ceil((1 << (params.WBitWidth - params.minWeightBits) * row_factor) + 1).W))

  val chunks_per_Product_bits =  RegInit(0.U(log2Ceil((((params.WBitWidth +  params.XBitWidth) ))  + 1).W))   

  //indexing extra registers 
  val mul1 = RegInit(0.U(16.W))
  val mul2 = RegInit(0.U(16.W)) 
  

  // A channel 
  val elements_to_read = RegInit(0.U((log2Ceil(max_w_elems) + 1).W))
  val chunk_addres     = RegInit(0.U(64.W))
  //D Channel 
  val elements_to_read_temp = RegInit(0.U((log2Ceil(max_w_elems) + 1).W))  
  val bytes_to_read         = RegInit(0.U(4.W))
  val reg_idx_start         = RegInit(0.U((log2Ceil(max_w_elems) + 1).W))  
  val offset_in_chunk_1     = RegInit(0.U(3.W))
  val offset_in_chunk_2     = RegInit(0.U(3.W))

  
  val step = params.x_slice * params.y_slice
  val step_1 = step / row_factor 
  val totalGroups_1= (totalSyncMems) / step_1
  val stepCycle_1 = RegInit(0.U((log2Ceil(totalGroups_1) + 1).W))

  val step_2 = step /row_factor   //TODO  make it work
  val totalGroups_2= totalSyncMems / step_2
  val stepCycle_2 = RegInit(0.U((log2Ceil(totalGroups_2) + 1).W))

  val maxCount = totalSyncMems / step_2  
  val product_counter = RegInit(0.U(log2Ceil(maxCount + 1).W))


  // ChunkSignExtender Module 
  val chunk_nums = maxParts // + 2
  val max_ch =  scala.math.max(step_1, step_2) 
  val chunkExtenders = Seq.fill(totalSyncMems,chunk_nums) { Module(new ChunkSignExtender(params.XBitWidth,params.minInputBits))}
  for {
    i <- 0 until totalSyncMems
    j <- 0 until chunk_nums
  } {
    val ext = chunkExtenders(i)(j)
        ext.io.data       := 0.U
        ext.io.input_bits := 0.U
        // ext.io.mask       := 0.U
        ext.io.idx        := 0.U
  }

  
  // DMA bus parameters (how many register or elements fitr in 64 bits dma channel)
   val elemsPerChunk_in = RegInit(0.U(5.W))  // holds up to 16  
   val RegsPerChunk_in  = RegInit((64 / params.XBitWidth).U(math.max(1, log2Ceil(64 / params.XBitWidth) + 1).W)) 
   val elemsPerChunk_w  = RegInit((64 / params.minInputBits).asUInt)  // and give it 6.W width
   val RegsPerChunk_w   = RegInit((64 / params.WBitWidth).U(math.max(1, log2Ceil(64 / params.WBitWidth) + 1).W))
   val  clmp = RegInit(0.U(64.W))  // holds up to 16  
 

  //new 
  val inputs_per_reg = RegInit((params.XBitWidth.U / input_bits))
  inputs_per_reg     := params.XBitWidth.U/input_bits 

  //A Chunk Offset Module 
  val chunkInfoModule_A = Module(new ChunkInfoModule_A(params,max_w_elems))
  //inputs 
  chunkInfoModule_A.io.cin_reg          := cin_reg
  chunkInfoModule_A.io.j_x              := 0.U
  chunkInfoModule_A.io.x_elems_per_reg  := x_elems_per_reg
  chunkInfoModule_A.io.elemsPerChunk_in := elemsPerChunk_in
  chunkInfoModule_A.io.adr_X            := adr_X
  chunkInfoModule_A.io.RegsPerChunk_in  := RegsPerChunk_in
  chunkInfoModule_A.io.mul1             := mul1
  chunkInfoModule_A.io.mul2             := mul2

  //D Chunk Offset Module 
  val chunkInfoModule_D = Module(new ChunkInfoModule_D(params,max_w_elems,max_x_regs))
  //inputs 
  chunkInfoModule_D.io.cin_reg          := cin_reg
  chunkInfoModule_D.io.j_x_temp         := 0.U
  chunkInfoModule_D.io.x_elems_per_reg  := x_elems_per_reg
  chunkInfoModule_D.io.elemsPerChunk_in := elemsPerChunk_in
  chunkInfoModule_D.io.RegsPerChunk_in  := RegsPerChunk_in
  chunkInfoModule_D.io.dma_counter_d    := 0.U
  chunkInfoModule_D.io.mul1             := mul1
  chunkInfoModule_D.io.mul2             := mul2 
  chunkInfoModule_D.io.input_bits       := input_bits
  chunkInfoModule_D.io.max_bits         := 0.U 



  // Elaboration time parameters to avoid div(/) and mod(%)
  val x_slice: Int = params.x_slice  // must be power of 1,2, e.g. 16
  val x_slice_mask = (x_slice - 1).U
  val x_slice_log2 = log2Ceil(x_slice)
  val max_val = 16 * x_slice // maybe 64 here ? TODO 
  val max_mask = (max_val - 1).U
  val mems_per_ys = params.x_slice/row_factor 

  val total_blocks_per_y =  params.x_slice.U * blocks_in_Sync_mem_2  
  blocks_in_Sync_mem_2 :=  blocks_in_Sync_mem_1

  //----------------------------->  Pipeline Stages in Select Phase  Registers 

  // stage 1 Output Registers 
    val xThXRegVec = Reg(Vec(totalSyncMems, UInt(max_val.W)))           
    val yThXRegVec = Reg(Vec(totalSyncMems, UInt(log2Ceil(params.y_slice).W)))
    val shiftedVec = Reg(Vec(totalSyncMems, UInt(params.WBitWidth.W)))
    val W_off_Vec  = Reg(Vec(totalSyncMems, UInt(5.W)))
    val Data_Vec   = Reg(Vec(totalSyncMems, UInt(params.WBitWidth.W)))


    val N =  (params.DMA_bits/params.XBitWidth)*totalSyncMems
    val bankSize = params.DMA_bits/params.WBitWidth


    val N_in = (params.DMA_bits/params.XBitWidth)*totalSyncMems
    val bankSize_in = params.DMA_bits/params.XBitWidth 
    val numBanks_in    = totalSyncMems.U // (N_in/ bankSize_in).U



    val WRegIdxdVec = Reg(Vec(totalSyncMems, UInt(log2Ceil(N).W))) // make WBitWidth ≥ log2Ceil(N)
    val Valid_In_Vec = Reg(Vec(totalSyncMems, Bool()))



  // stage 2 Output Registers 
    val weight_Sint_part_Reg = Reg(Vec(totalSyncMems, SInt(params.WBitWidth.W)))
    val abs_weight_uint_Reg  = Reg(Vec(totalSyncMems, UInt(params.WBitWidth.W)))
    val row_idx_Reg = Reg(Vec(totalSyncMems, UInt(log2Ceil(RowsPerBlock + 1).W)))
    val elemOffset_Reg       = Reg(Vec(totalSyncMems, UInt(4.W)))
    val w_reg_valid_temp_Reg = Reg(Vec(totalSyncMems, Bool()))

    //2 stages add tree 
    // Registers για partial sums (αποθηκεύονται στον πρώτο κύκλο)
    val partialSums = Reg(Vec(params.y_slice, Vec(2, SInt(Products(0).getWidth.W))))
    val cycle_2 = RegInit(false.B)


    //------------------------------------------
    // parameters
    val S = (params.DMA_bits / params.XBitWidth)
    val S_U = S.U
    val g   = blocks_in_Sync_mem_1

    // state for current active bank (group)
    val curBank     = RegInit(0.U(log2Ceil(totalSyncMems).W))
    val curElems    = RegInit(0.U(log2Ceil((1 << (params.WBitWidth - params.minWeightBits)) * params.Row_factor + 1).W)) // up to g
    val curRowIdx   = RegInit(0.U(log2Ceil(regs_per_mem/4).W))                       // row in X_MEM(curBank)
    val curLaneCnt  = RegInit(0.U(log2Ceil(S + 1).W))                                // lanes filled in current row [0..S]
    val curRowBuf   = RegInit(0.U(params.DMA_bits.W))   


    val S_w = (params.DMA_bits / params.WBitWidth)
    val S_U_w = S_w.U
    val g_w   = blocks_in_Sync_mem_1

    // state for current active bank (group)
    val curBank_w     = RegInit(0.U(log2Ceil(totalSyncMems).W))
    val curElems_w    = RegInit(0.U(log2Ceil((1 << (params.WBitWidth - params.minWeightBits)) * params.Row_factor + 1).W)) // up to g
    val curRowIdx_w    = RegInit(0.U(log2Ceil(regs_per_mem/8).W))                       // row in X_MEM(curBank)
    val curLaneCnt_w  = RegInit(0.U(log2Ceil(S + 1).W))                                // lanes filled in current row [0..S]
    val curRowBuf_w   = RegInit(0.U(params.DMA_bits.W))  
    //------------------------------------------


  // Define states for the FSM
  val sIdle :: sLoadX :: sGenerateRegFiles :: sLoadW_and_sSelectAndAccumulate :: sStoreOutput :: sDELAY :: Nil = Enum(6)
  val state = RegInit(sIdle)

  //If cmd.ready is hardcoded to true.B, the CPU ignores io.busy  SOS 
  io.busy     := (state =/= sIdle) 
  cmd.ready   := (state === sIdle)
  
   switch(state) {
    is(sIdle) {
      when(cmd.fire && (mode_0)) {        
        adr_X         := rs1
        x_slice_Reg   := rs2

        //DMA init 
        dma_send      := true.B
        dma_resp      := true.B
        dma_counter_a := 0.U 
        dma_counter_d := 0.U 

        //indexer init 
        i_x       := 0.U 
        j_x       := 0.U 
        i_x_start := 0.U 
        j_x_start := 0.U 
        loops_cnt := 0.U 
        i_o       := 0.U 
        j_o       := 0.U 
        i_w_temp  := 0.U 
        j_x_temp  := 0.U 

        valid_inputs := 0.U
        valid_weights := VecInit(Seq.fill(buffers)(0.U((totalSyncMems*64/params.WBitWidth).W)))
        
        // XS YS Registers Init 
        val mux = Mux(rin_reg >= y_slice_Reg,y_slice_Reg,rin_reg)
        YS      := mux
        YS_b    := mux
        elemsPerChunk_in :=  64.U / input_bits  // Mux(input_bits === 8.U , 8.U , 16.U)  
        elemsPerChunk_w  :=  64.U /weight_bits   // Mux(weight_bits === 8.U , 8.U , 16.U)  


        // X_reg := VecInit(Seq.fill(params.y_slice)(VecInit(Seq.fill(max_x_regs)(0.S(params.XBitWidth.W)))))
      } 

      when(cmd.fire && (mode_1)) {
        adr_W     := rs1

        val scaledSlice        = rs2(15, 0)   // 16 bits
        x_slice_weights_reg   := rs2(31, 16)  // 16 bits
        dma_limit             := rs2(47, 32)  // 16 bits

        // ///-------------------------------

        // // how many logical blocks can  and need tp mapp in memory blocks  
        // val clamped_new        = Mux(cin_reg >= scaledSlice, scaledSlice, cin_reg)

        // //every memory blocks can fit this many logical blockst stakced  
        // val logical_blocks_in_mem_block      = ( 1.U << (params.WBitWidth.U - weight_bits ) ) * row_factor.U  * params.Mem_row_factor.U  // How many blocks can fit in one psysical memory 
        
        // //split equal logical blocks to all memory blocks 
        // val floor_div = clamped_new / mems_per_ys.U 

        // val clamped  =  floor_div * mems_per_ys.U 

        // ///-------------------------------
        clmp := scaledSlice
        val clamped        = Mux(cin_reg >= scaledSlice, scaledSlice, cin_reg) 
        val elems_per_word = params.XBitWidth.U/input_bits  // Mux(input_bits === 4.U, params.XBitWidth.U >> 2, params.XBitWidth.U >> 3)
        val temp_mul       = clamped * elems_per_word

        x_slice_input_reg := scaledSlice
        XS                := clamped
        XS_b              := clamped

        x_elems_per_reg := elems_per_word // params.XBitWidth.U/input_bits 
        w_elems_per_reg := params.WBitWidth.U/weight_bits  //Mux(weight_bits === 4.U, params.WBitWidth.U >> 2, params.WBitWidth.U >> 3)

        //before load X
        all_available_elems_x := (temp_mul).min(cin_reg)  //( clamped * (params.XBitWidth.U/input_bits )).min(cin_reg - j_x) 
        mul1                  := 0.U
        mul2                  := temp_mul

        //dynamic bitwitdh parameters 
        val p_bits = input_bits + weight_bits
        mask_in                 := (1.U << input_bits) - 1.U
        chunks_per_Product_bits := p_bits
        mask_p                  := (1.U << p_bits) - 1.U
        mask_w                  := (1.U << weight_bits) - 1.U 

        val block_rows              = (1.U << (weight_bits  -2.U )) 
        block_rows_1              :=   block_rows
        block_rows_2              :=   block_rows


        val blocks_in_Sync_mem_max      = ( 1.U << (params.WBitWidth.U - weight_bits ) ) * row_factor.U  * params.Mem_row_factor.U  // How many blocks can fit in one psysical memory 
        // mems_per_ys    // How may psysical mems have for one actvation vector 
        // val help = clamped / F 
        val help =  (clamped + mems_per_ys.U - 1.U) / mems_per_ys.U //ceiled  

        val blocks_in_Sync_mem = Mux(help < blocks_in_Sync_mem_max, help,blocks_in_Sync_mem_max )

        //////////////////
        // val rounded_blocks = ((blocks_in_Sync_mem + 3.U) >> 2) << 2 //TODO 
        // blocks_in_Sync_mem_1 := rounded_blocks 
        //////////////////

        blocks_in_Sync_mem_1 := blocks_in_Sync_mem 
        
        //input
        chunkInfoModule_A.io.x_elems_per_reg := elems_per_word
        chunkInfoModule_A.io.mul1 := 0.U 
        chunkInfoModule_A.io.mul2 := temp_mul   

        //output 
        elements_to_read := chunkInfoModule_A.io.elements_to_read 
        chunk_addres     := chunkInfoModule_A.io.chunk_address
         
        //input
        chunkInfoModule_D.io.x_elems_per_reg  := elems_per_word
        chunkInfoModule_D.io.elemsPerChunk_in := elemsPerChunk_in
        chunkInfoModule_D.io.RegsPerChunk_in  := RegsPerChunk_in
        chunkInfoModule_D.io.mul1             := 0.U
        chunkInfoModule_D.io.mul2             := temp_mul
        chunkInfoModule_D.io.input_bits       := input_bits
        chunkInfoModule_D.io.max_bits         := params.XBitWidth.U



        //output 
        bytes_to_read         :=  chunkInfoModule_D.io.bytes_to_read 
        reg_idx_start         :=  chunkInfoModule_D.io.reg_idx_start
        offset_in_chunk_1     :=  chunkInfoModule_D.io.offset_in_chunk_1
        elements_to_read_temp := chunkInfoModule_D.io.elements_to_read_temp

      }

      when(cmd.fire && (mode_2)) {
        adr_O    := rs1
        cout_reg := rs2  
      }

      when(cmd.fire && (mode_3)) {
        y_slice_Reg := rs2 
        cin_reg := rs1 
      } 
      
      when(cmd.fire && (mode_5)) {
        LOOPS   := rs1 
        rin_reg := rs2
      }
       
     if(params.SCALE) { //SCALE  
      when(cmd.fire && (mode_7)) {
        scale   := rs1
      }
     }

      when(cmd.fire && (mode_8)) {
        input_bits   := rs1(5,0)
        weight_bits  := rs1(11,6)
        output_bits  := rs1(17,12)

      if(params.DEBUG){
        PC_loadX    := 0.U 
        PC_Generate := 0.U 
        PC_loadW    := 0.U 
        PC_Select   := 0.U 
        PC_storeO   := 0.U 
      }

      } 

      when(cmd.fire && mode_10) { 
        state := sLoadX
        valid_inputs := 0.U 
      }

    }
  is(sLoadX) {
  if(params.DEBUG){  
    PC_loadX := PC_loadX + 1.U 
  }
  

  when(dma_send && !dma.io.busy ) { //send request to DMA in A Channel  
    
    val j_x_next = j_x + elements_to_read 
    
    dma.io.mode   := false.B  
    dma.io.addr   := chunk_addres
    dma_counter_a := dma_counter_a + elements_to_read            
    dma.io.valid  := true.B
    dma_send      := dma_counter_a < all_available_elems_x  - elements_to_read
    j_x           := j_x_next 


    chunkInfoModule_A.io.j_x := j_x_next
    elements_to_read := chunkInfoModule_A.io.elements_to_read 
    chunk_addres     := chunkInfoModule_A.io.chunk_address

  }

  when(dma.io.d_valid) { //response fron DMA in D channel 

    val j_x_temp_next      = j_x_temp + elements_to_read_temp
    val dma_counter_d_next = dma_counter_d + elements_to_read_temp

    // val data  = dma.io.readData                                                                                                         // UInt(64.W)
    // val bytes = VecInit(Seq.tabulate(64 / params.XBitWidth)(i => data((i + 1) *  params.XBitWidth - 1, i *  params.XBitWidth).asSInt))

    // for (i <- 0 until 64 / params.XBitWidth) {
    //   // when(i.U < bytes_to_read ) { //|| input_bits === params.XBitWidth.U 
    //     X_reg(Rin_cnt)(reg_idx_start + i.U) :=   bytes(offset_in_chunk_1 + i.U) 
    //     // O_reg(0)(Rin_cnt)(reg_idx_start + i.U) := bytes(offset_in_chunk_1 + i.U) 
    //   // }
    // }

    // ------------- Sync Read Memory System ------------------ // 

    // counter_row  from 0 to blocks_in_Sync_mem_1 - 1 and then reset  
    // counter_mem  from 0 to totalsynmem -1 and then reset 
    // offset from 0 to 64/16 
    // every row have  more X_reg elements 
     
  //  require(params.Mem_row_factor >= 4, s"Mem_row_factor must be >= 4, got ${params.Mem_row_factor}")
   //need as input mem*4 Cin  at least  TODO (fix this)
   //Στην καλυτερη γραψε 64 bit σε καθε μνημη το λιγοτερη ειναι ενα θεμα 
   //blocks_in_Sync_mem_1 να ειναι μγεαλυτερο ίσο του 4 

   valid_inputs := valid_inputs + elements_to_read_temp   // / (params.XBitWidth.U/input_bits)   //* (input_bits /params.XBitWidth.U)

    //  X_DATA := data
    
    val data  = dma.io.readData                                                                                                         // UInt(64.W)
    
    // loop through all memories
    for (i <- 0 until totalSyncMems) {
      when (counter_mem === i.U) {
        X_MEM(i).write(counter_row, data)
      }
    }  //counter_mem === totalSyncMems.U

    // update counters
    
    // when (counter_reg >= (blocks_in_Sync_mem_1 - (params.DMA_bits/params.XBitWidth).U )) { 
    when (counter_reg.asSInt >=  (blocks_in_Sync_mem_1.asSInt - (params.DMA_bits / params.XBitWidth).S)) {
      counter_row   := 0.U
      counter_reg   := 0.U 
      counter_mem := counter_mem + 1.U
    } .otherwise {
      counter_row := counter_row + 1.U
      counter_reg := counter_reg +  (params.DMA_bits/params.XBitWidth).U
    } 

    // //------------------------------------------------------
    
    //   // scala
    //   val data64 = dma.io.readData
    //   val lanes  = VecInit(Seq.tabulate(S)(i => data64((i + 1) * params.XBitWidth - 1, i * params.XBitWidth)))

    //   val capInGroup = g - curElems
    //   val n0Total    = Mux(capInGroup <= S_U, capInGroup, S_U)   // to current bank (this beat)
    //   val n1         = S_U - n0Total                             // spill to next bank

    //   // 1) place as many as fit in the current row
    //   val spaceInRow = S_U - curLaneCnt
    //   val n0a        = Mux(n0Total <= spaceInRow, n0Total, spaceInRow) // into current row

    //   val addCurA = (0 until S).foldLeft(0.U(params.DMA_bits.W)) { (acc, i) =>
    //     val take      = i.U < n0a
    //     val shiftBits = (curLaneCnt + i.U) * params.XBitWidth.U
    //     acc | Mux(take, (lanes(i.U) << shiftBits), 0.U)
    //   }
    //   val bufA   = curRowBuf | addCurA
    //   val laneA  = curLaneCnt + n0a
    //   val fullA  = laneA === S_U

    //   // 2) leftover lanes for same bank → start next row (combinational)
    //   val rem0 = n0Total - n0a
    //   val addCurB = (0 until S).foldLeft(0.U(params.DMA_bits.W)) { (acc, i) =>
    //     val iU        = i.U
    //     val take      = iU < rem0
    //     val shiftBits = iU * params.XBitWidth.U
    //     val srcIdx    = iU + n0a
    //     acc | Mux(take, (lanes(srcIdx) << shiftBits), 0.U)
    //   }

    //   // “after-this-beat” combinational view for current bank
    //   val bufAfter  = Mux(rem0 === 0.U, bufA, addCurB)
    //   val laneAfter = Mux(rem0 === 0.U, laneA, rem0)
    //   // if we filled rowA this cycle, the partial row (if any) lives in the next row index
    //   val rowToWriteAfter = Mux(fullA, curRowIdx + 1.U, curRowIdx)

    //   // 3) update regs for current bank row staging
    //   when (fullA) {
    //     // wrote rowA
    //     for (i <- 0 until totalSyncMems) { when (curBank === i.U) { X_MEM(i).write(curRowIdx, bufA) } }
    //     curRowIdx := curRowIdx + 1.U
    //     // stage next row if rem0 > 0
    //     curRowBuf  := Mux(rem0 === 0.U, 0.U, addCurB)
    //     curLaneCnt := Mux(rem0 === 0.U, 0.U, rem0)
    //   } .otherwise {
    //     curRowBuf  := bufA
    //     curLaneCnt := laneA
    //   }

    //   // 4) group progress and flush on boundary using the after-this-beat wires
    //   val groupDone = (curElems + n0Total) === g
    //   curElems := Mux(groupDone, 0.U, curElems + n0Total)

    //   when (groupDone) {
    //     // flush the partial row of this bank (the “5th” element case)
    //     when (laneAfter =/= 0.U) {
    //       for (i <- 0 until totalSyncMems) {
    //         when (curBank === i.U) { X_MEM(i).write(rowToWriteAfter, bufAfter) }
    //       }
    //     }
    //     // clear staging and advance bank
    //     curRowBuf  := 0.U
    //     curLaneCnt := 0.U
    //     curBank    := Mux(curBank === (totalSyncMems - 1).U, 0.U, curBank + 1.U)
    //     curRowIdx  := 0.U
    //   }

    //   // 5) spill to next bank (remaining lanes in this beat)
    //   when (n1 =/= 0.U) {
    //     val nextBank = Mux(groupDone, Mux(curBank === (totalSyncMems - 1).U, 0.U, curBank + 1.U), curBank)
    //     val addNext = (0 until S).foldLeft(0.U(params.DMA_bits.W)) { (acc, i) =>
    //       val iU        = i.U
    //       val inRange   = iU >= n0Total
    //       val relPos    = iU - n0Total
    //       val shiftBits = relPos * params.XBitWidth.U
    //       acc | Mux(inRange, (lanes(iU) << shiftBits), 0.U)
    //     }
    //     curBank    := nextBank
    //     curRowIdx  := Mux(groupDone, 0.U, curRowIdx)
    //     curRowBuf  := addNext
    //     curLaneCnt := n1
    //     curElems   := n1
    //   }
    // //------------------------------------------------------

    
    // ------------- Sync Read Memory System ------------------ // 

    dma_counter_d := dma_counter_d_next
    dma_resp      := dma_counter_d < all_available_elems_x - elements_to_read_temp
    j_x_temp      := j_x_temp_next


    //input 
    chunkInfoModule_D.io.j_x_temp      := j_x_temp_next
    chunkInfoModule_D.io.dma_counter_d := dma_counter_d_next
    chunkInfoModule_D.io.input_bits       := input_bits
    chunkInfoModule_D.io.max_bits         := params.XBitWidth.U

    //output 
    bytes_to_read         := chunkInfoModule_D.io.bytes_to_read
    reg_idx_start         := chunkInfoModule_D.io.reg_idx_start
    offset_in_chunk_1     := chunkInfoModule_D.io.offset_in_chunk_1
    elements_to_read_temp := chunkInfoModule_D.io.elements_to_read_temp
  }

  when(!dma_resp) { 
      dma_send      := true.B && (Rin_cnt < YS -1.U)
      dma_resp      := true.B && (Rin_cnt < YS -1.U)   
      dma_counter_a := 0.U 
      dma_counter_d := 0.U 
      Rin_cnt       := Rin_cnt + 1.U 
      j_x           := Mux(Rin_cnt < YS -1.U,j_x_start,j_x)
      i_x           := Mux(Rin_cnt < YS -1.U,i_x + 1.U,i_x)

      mul1          := Mux(Rin_cnt < YS -1.U,cin_reg*(i_x + 1.U),mul1)

      //before load X 
      // val x_slice_input_reg_help = blocks_in_Sync_mem_1 * x_slice_input_reg 
      // val XS_temp   = Mux(cin_reg - j_x_start  >= x_slice_input_reg_help,x_slice_input_reg_help,cin_reg - j_x_start ) //debug 
      val XS_temp   = Mux(cin_reg - j_x_start  >= x_slice_input_reg,x_slice_input_reg,cin_reg - j_x_start ) //comments out 
      val mul1_temp = (i_x + 1.U) * cin_reg
     
     //compute loigc 
     //   val mul2_temp = XS_temp * x_elems_per_reg

     //mux and shift logic
     val mul2_temp = Mux(x_elems_per_reg === 1.U, XS_temp, XS_temp << 1)

      XS            := XS_temp
      mul2          := mul2_temp
      mul1          := mul1_temp

      //before load X
      val bit_offset_in_byte = ((mul1_temp + j_x_start) * input_bits) & (params.XBitWidth.U - 1.U) //( ((i_x * cin_reg) + j_x) * input_bits) %  params.XBitWidth.U
      val skipInFirstByte = bit_offset_in_byte / weight_bits //Mux(input_bits === 4.U, bit_offset_in_byte >> 2, bit_offset_in_byte >> 3) //bit_offset_in_byte / input_bits 
      // dirty_elems_x          := skipInFirstByte

      //++
      val elems_per_word = params.XBitWidth.U/input_bits  // Mux(input_bits === 4.U, params.XBitWidth.U >> 2, params.XBitWidth.U >> 3)
      all_available_elems_x := (XS * elems_per_word).min(cin_reg - j_x_start)  //( XS * (params.XBitWidth.U/input_bits )).min(cin_reg - j_x) 
      j_x_temp              := j_x_start

    //  val mul1_temp = mul1 
    //  val mul2_temp = mul2

      chunkInfoModule_A.io.j_x  := j_x_start
      chunkInfoModule_A.io.mul1 := mul1_temp
      chunkInfoModule_A.io.mul2 := mul2_temp
      elements_to_read          := chunkInfoModule_A.io.elements_to_read
      chunk_addres              := chunkInfoModule_A.io.chunk_address

      //input
      chunkInfoModule_D.io.j_x_temp         := j_x_start
      chunkInfoModule_D.io.mul1             := mul1_temp
      chunkInfoModule_D.io.mul2             := mul2_temp
      chunkInfoModule_D.io.input_bits       := input_bits
      chunkInfoModule_D.io.max_bits         := params.XBitWidth.U

      //output 
      bytes_to_read         :=  chunkInfoModule_D.io.bytes_to_read 
      reg_idx_start         :=  chunkInfoModule_D.io.reg_idx_start
      offset_in_chunk_1     :=  chunkInfoModule_D.io.offset_in_chunk_1
      elements_to_read_temp := chunkInfoModule_D.io.elements_to_read_temp


      // ------------- Sync Read Memory System ------------------ // 
      for (sync_mem_idx <- 0 until totalSyncMems) { 
        X_vals(sync_mem_idx) := X_MEM(sync_mem_idx).read(0.U, Rin_cnt + 1.U === YS )
      }
      // ------------- Sync Read Memory System ------------------ // 

  }



  when(Rin_cnt === YS){  
      val x_slice_reg_elems = x_slice_input_reg * x_elems_per_reg
      load_x_cnt := load_x_cnt + 1.U
      j_x_start  := Mux(j_x_start + x_slice_reg_elems   >= cin_reg , 0.U,j_x_start + x_slice_reg_elems )
      i_x_start  := Mux(j_x_start + x_slice_reg_elems   >= cin_reg , i_x_start + YS -0.U,i_x_start)
      cycleCount_1 := 0.U
      Products.foreach(_ := 0.U)
      stepCycle_1     := 0.U 


      ////////////////////////////////////////////////////////////////////////////////////
      val remain_in_column      = cin_reg - i_w
       val remain_in_column_regs = (remain_in_column + w_elems_per_reg -1.U)/w_elems_per_reg
      // val remain_in_column_regs = Mux(w_elems_per_reg === 1.U,remain_in_column,(remain_in_column + w_elems_per_reg -1.U)/w_elems_per_reg)                  // (remain_in_column + w_elems_per_reg -1.U)/w_elems_per_reg 
      val XS_W_temp             = Mux(remain_in_column_regs >= x_slice_weights_reg,x_slice_weights_reg,remain_in_column_regs)  //max_w_elems
      XS_W  := XS_W_temp
      // W_reg := VecInit(Seq.fill(params.num_filters)(VecInit(Seq.fill(max_w_elems)(VecInit(Seq.fill(buffers)(0.S(params.WBitWidth.W)))))))  //new 

      dirty_elems_w.foreach(_ := 0.U)

      //before load W
      val temp_mul1 = j_w * cin_reg 
      val temp_mul2  = XS_W_temp * w_elems_per_reg 
      mul1 := temp_mul1 
      mul2 := temp_mul2
      val bit_offset_in_byte = (((temp_mul1)) * weight_bits) & (params.WBitWidth.U - 1.U)
      val skipInFirstByte    = bit_offset_in_byte / weight_bits
      // dirty_elems_w(0)    := skipInFirstByte
      all_available_elems := ( temp_mul2).min(cin_reg - i_w)

      //input 
      chunkInfoModule_A.io.j_x              := i_w
      chunkInfoModule_A.io.x_elems_per_reg  := w_elems_per_reg
      chunkInfoModule_A.io.elemsPerChunk_in := elemsPerChunk_w
      chunkInfoModule_A.io.adr_X            := adr_W
      chunkInfoModule_A.io.RegsPerChunk_in  := RegsPerChunk_w
      chunkInfoModule_A.io.mul1             := temp_mul1
      chunkInfoModule_A.io.mul2             := temp_mul2

      //output 
      elements_to_read := chunkInfoModule_A.io.elements_to_read
      chunk_addres     := chunkInfoModule_A.io.chunk_address

      //input 
      chunkInfoModule_D.io.j_x_temp         := i_w
      chunkInfoModule_D.io.x_elems_per_reg  := w_elems_per_reg
      chunkInfoModule_D.io.elemsPerChunk_in := elemsPerChunk_w
      chunkInfoModule_D.io.RegsPerChunk_in  := RegsPerChunk_w
      chunkInfoModule_D.io.mul1             := temp_mul1
      chunkInfoModule_D.io.mul2             := temp_mul2
      chunkInfoModule_D.io.input_bits       := weight_bits 
      chunkInfoModule_D.io.max_bits         := params.WBitWidth.U

      //output 
      bytes_to_read         := chunkInfoModule_D.io.bytes_to_read
      reg_idx_start         := chunkInfoModule_D.io.reg_idx_start
      offset_in_chunk_2     := chunkInfoModule_D.io.offset_in_chunk_1
      elements_to_read_temp := chunkInfoModule_D.io.elements_to_read_temp

      dma_send      := true.B
      dma_resp      := true.B
      dma_counter_a := 0.U
      dma_counter_d := 0.U
      w_count       := 0.U 
      i_w_temp      := i_w

      if(params.SimpleCache4x8) {  
        state := Mux(input_bits === 4.U && weight_bits === 8.U, sLoadW_and_sSelectAndAccumulate, sGenerateRegFiles )
      } else { 
        state      := sGenerateRegFiles 
        Products.foreach(_ := 0.U)
      }
    
    // ------------- Sync Read Memory System ------------------ // 
  
      counter_row := 0.U //TODO 
      counter_mem := 0.U 
      offset_counter := 0.U 

    // ------------- Sync Read Memory System ------------------ // 


  }

}
    
is(sGenerateRegFiles) { 
      if(params.DEBUG){  
        PC_Generate := PC_Generate + 1.U
      }

      val shiftAmt  = Log2(block_rows_1)  //  block_rows_2 = {1,2,64}
      val start_row = counter_1 << shiftAmt 
      // val shift_blocks = Log2(blocks_in_Sync_mem_1)
      // val shift_y      = Log2(total_blocks_per_y)

   
  for (sync_mem_idx <- 0 until totalSyncMems) { 

        // // mapping to 2D space  -- power of 2 optimizations --  (x_slice,blocks_in_Sync_mem) power of two 
        // val global_block_idx = (sync_mem_idx.U << shift_blocks) + counter_1
        // val x_th_x_reg = global_block_idx & (total_blocks_per_y - 1.U)
        // val y_th_x_reg = global_block_idx >> shift_y

        // mapping to 2D space -- compute logic -- 
        // val global_block_idx   =  sync_mem_idx.U * blocks_in_Sync_mem_1 + counter_1 
        // val x_th_x_reg = global_block_idx % total_blocks_per_y
        // val y_th_x_reg  = global_block_idx / total_blocks_per_y 

        when(cycleCount_1 =/= 0.U ) { 
          val finalAddressInBRAM =  start_row + (cycleCount_1 -1.U)   
          temp_muls_mem(sync_mem_idx).write(finalAddressInBRAM, Products(sync_mem_idx)) 
        }  


        //////////////////////////////////////////////// -- edition 1 

        // mapping to 2D space  -- power of 2 optimizations --  (x_slice,blocks_in_Sync_mem) power of two 
        //val global_block_idx = (sync_mem_idx.U << shift_blocks) + counter_1
        val global_block_idx   =  sync_mem_idx.U * blocks_in_Sync_mem_1 + counter_1 
        val x_th_x_reg = global_block_idx + dirty_elems_x //& (total_blocks_per_y - 1.U) // TODO 
        // val y_th_x_reg =
        //       if (params.y_slice > 1)
        //         global_block_idx >> shift_y
        //       else
        //         0.U

        //computation version
        // val wrapped     = x_th_x_reg % N_in.U 
        // val bank_in     = wrapped / bankSize_in.U
        // val byte_index  = wrapped % bankSize_in.U     

        // val rows_per_bank = (blocks_in_Sync_mem_1 + bankSize_in.U - 1.U) / bankSize_in.U
        // val row_in_bank  = (x_th_x_reg / bankSize_in.U) % rows_per_bank
        // val byte_index   = x_th_x_reg % bankSize_in.U
        // val bank_in      = (x_th_x_reg / (bankSize_in.U * rows_per_bank)) % totalSyncMems.U

        //--------------------------------- new code 
        val S = bankSize_in.U
        val B = totalSyncMems.U

        val rows_used_per_bank =  (blocks_in_Sync_mem_1 + S - 1.U) / S // ceil(blocks_in_Sync_mem_1 / S)
        
        // x_th_x_reg is your linear element index
        val chunkIdx    = x_th_x_reg / S
        val bank_in     = (chunkIdx / rows_used_per_bank) % B
        val row_in_bank = chunkIdx % rows_used_per_bank
        val byte_index  = x_th_x_reg % S


        // // group-per-bank
        // val S = bankSize_in.U                 // elems per row
        // val B = totalSyncMems.U               // banks
        // val g = blocks_in_Sync_mem_1          // elems per bank (group)

        // // x_th_x_reg is linear index
        // val groupIdx    = x_th_x_reg / g
        // val posInBank   = x_th_x_reg % g
        // val bank_in     = groupIdx % B
        // val row_in_bank = posInBank / S
        // val byte_index  = posInBank % S
        //--------------------------------- new code 



        val data_64 =  X_vals(bank_in)
        val bytes = VecInit(Seq.tabulate(params.DMA_bits / params.XBitWidth)(i => data_64((i + 1) *  params.XBitWidth - 1, i *  params.XBitWidth)))
        val elem_start  = x_th_x_reg * inputs_per_reg
        // val valid_x = valid_inputs >= elem_start
        val data  = bytes(byte_index) //Mux(valid_x,bytes(byte_index),0.U)    

        ////////////////////////////////////////////////

        // ------------- Sync Read Memory System ------------------ //  -- edition 2
        // val data_64 = X_vals(sync_mem_idx)
        // val bytes = VecInit(Seq.tabulate(64 / params.XBitWidth)(i => data_64((i + 1) *  params.XBitWidth - 1, i *  params.XBitWidth)))
        // val data = bytes(offset_counter)     
        // ------------- Sync Read Memory System ------------------ // 
            
        // val data      =   X_reg(y_th_x_reg)(x_th_x_reg).asUInt  // width = XBITWIDTH.W

        val P_data    =   Products(sync_mem_idx) 
        val P_slices  =   Wire(Vec(maxParts, UInt((product_bitwidth).W)))
          
        //split 
        for (i <- 0 until maxParts) {

          val extender = chunkExtenders(sync_mem_idx)(i)
          extender.io.data       := data
          extender.io.input_bits := input_bits
          extender.io.idx        := i.U
          val signedChunk = extender.io.out

         val absVal_new = Mux(signedChunk < 0.S, (-signedChunk), signedChunk)
         //
         val valid_x = valid_inputs + dirty_elems_x > elem_start + i.U // new
         val absVal = Mux(valid_x ,absVal_new.asUInt,0.U) 

          val chunk_p   = (P_data >> (i.U * chunks_per_Product_bits)) & mask_p  // UInt(input_bits.W)
          val scaled    = (absVal << 1) 
          P_slices(i)   := Mux(i.U < x_elems_per_reg , chunk_p + scaled,chunk_p)

        }

        val packed = Wire(UInt(product_bitwidth.W))
        var acc: UInt = 0.U

        //Concat and Accumulate 
        for (i <- 0 until maxParts) {
          val shiftAmt = i.U * chunks_per_Product_bits
          val shifted  = P_slices(i) << shiftAmt
          acc = acc | shifted
        }

        packed := acc
        Products(sync_mem_idx) :=  packed 
      }   

      cycleCount_1 := cycleCount_1 + 1.U      

    when(cycleCount_1 === block_rows_1) { 
        counter_1    := counter_1 + 1.U
        cycleCount_1 := 0.U
        Products.foreach(_ := 0.U)
          
      // ------------- Sync Read Memory System ------------------ // 
      offset_counter := Mux(offset_counter === 3.U, 0.U, offset_counter + 1.U)
      counter_row    := Mux(offset_counter === 3.U, counter_row + 1.U, counter_row)
      // ------------- Sync Read Memory System ------------------ // 

    } 
    
    // ------------- Sync Read Memory System ------------------ // 

    // Request phase (cycle N)
    val enable_read = ((cycleCount_1 === block_rows_1 - 2.U) && (offset_counter === 3.U)) || (block_rows_1 === 1.U) 
    val row         = Mux(offset_counter === 3.U, counter_row + 1.U, counter_row)

    // Read request
    val readData = Seq.tabulate(totalSyncMems)(i => X_MEM(i).read(row, enable_read))

    // Delay the enable signal by 1 cycle
    val read_valid = RegNext(enable_read, init=false.B)

    // Capture results (cycle N+1)
    when (read_valid) {
      for (i <- 0 until totalSyncMems) {
        X_vals(i) := readData(i)
      }
    }
    // ------------- Sync Read Memory System ------------------ // 
  
    when(counter_1 === blocks_in_Sync_mem_1 - 1.U && cycleCount_1 === (block_rows_1 )) { //4bits => 7 cycles delay   
        Rin_cnt       := 0.U
        j_x           := j_x_start
        i_x           := i_x_start
        cycleCount_1  := 0.U
        counter_1     := 0.U

        READ_ON       := true.B
        read_on_delay := false.B  
        delay         := true.B
        delay_counter := 0.U
        stepCycle_2 := 0.U 

        state            := sLoadW_and_sSelectAndAccumulate

        // ------------- Sync Read Memory System ------------------ //   
        counter_row := 0.U 
        counter_mem := 0.U 
        offset_counter := 0.U   
        // ------------- Sync Read Memory System ------------------ // 

    }       
       
    }
is(sLoadW_and_sSelectAndAccumulate) {

  if(params.DEBUG){  
    PC_loadW_and_Select := PC_loadW_and_Select + 1.U 
  }  

  
  when(buffers_mode.reduce(_ || _)) { // Select and accumualte Phase 

  val buff_index = select_buff_idx //Mux(buffers(0), 0.U, 1.U)

  if(params.DEBUG){  
      PC_Select := PC_Select + 1.U 
  } 

      // ------------- Sync Read Memory System ------------------ // 

      // Request phase (cycle N)
      val enable_read = READ_ON && (offset_counter === 0.U) 
      val row         = Mux(offset_counter === 3.U, counter_row + 1.U, counter_row)

      // Read request
      val readData = Seq.tabulate(totalSyncMems)(i => X_MEM(i).read(row, enable_read))

      // Delay the enable signal by 1 cycle
      val read_valid = RegNext(enable_read, init=false.B)

      // Capture results (cycle N+1)
        for (i <- 0 until totalSyncMems) {
            X_vals(i) := readData(i)

        }

      val offset_counter_delay = RegNext(RegNext(offset_counter))

      when (offset_counter === 3.U && READ_ON && cycleCount_2 === x_elems_per_reg - 1.U) { 
        offset_counter := 0.U
        counter_row    := counter_row + 1.U
      }.otherwise {
        // offset_counter := offset_counter + 1.U
        offset_counter    := Mux(cycleCount_2 === x_elems_per_reg - 1.U ,offset_counter + 1.U,offset_counter)

      }

      // ------------- Sync Read Memory System ------------------ // 
      val enable_read_w = READ_ON && (elements_counter === 0.U ) // (elements_counter === w_elems_per_chunk *totalSyncMems.U - totalSyncMems.U)

      val W_wire = Wire(Vec(buffers, Vec(totalSyncMems, UInt(64.W))))

      for (b <- 0 until buffers) { 
        val valid_read = (buff_index === b.U) && enable_read_w
        for (i <- 0 until totalSyncMems) {
           W_wire(b)(i) :=  W_MEM(b)(i).read(row_weight,valid_read) 
        }
      } 
      
      when(elements_counter === w_elems_per_chunk *totalSyncMems.U - totalSyncMems.U ){ 
        elements_counter := 0.U 
        row_weight := row_weight + 1.U 
      }.otherwise { 
        elements_counter := elements_counter + totalSyncMems.U 
      }
       
      // ------------- Sync Read Memory System ------------------ // 

 
          val shiftAmt  = Log2(block_rows_2) // block_rows_2 = {1,2,64}
          val start_row = RegNext(counter_2 << shiftAmt) 
          val shift_blocks = Log2(blocks_in_Sync_mem_2)
          val shift_y      = Log2(total_blocks_per_y)


        //  ------------------------------------------------------------------------------------------------> stage - 1 
          val shift_amt_w = Log2(w_elems_per_reg)   // 1→0, 2→1, 4→2
          val shift_amt_w_new = 3.U - shift_amt_w
          val w_mask      = w_elems_per_reg - 1.U   // mask for w_offset bits 
          val x_shift = Mux(x_elems_per_reg === 1.U, 0.U, 1.U)

          for (sync_mem_idx <- 0 until totalSyncMems) {

            // ---------------------------------------------
            // 1. Compute Global Indices for Weights & Activations
            // ---------------------------------------------

            // mapping to 2D space  -- power of 2 optimizations --  (x_slice,blocks_in_Sync_mem) power of two 
            val global_block_idx   =  sync_mem_idx.U * blocks_in_Sync_mem_2 + counter_2 
            // val global_block_idx = (sync_mem_idx.U << shift_blocks) + counter_2
            val x_th_x_reg = global_block_idx  // & (total_blocks_per_y - 1.U) // TODO
            val y_th_x_reg =
              if (params.y_slice > 1)
                global_block_idx >> shift_y
              else
                0.U
             
            val x_th_real = x_th_x_reg + dirty_elems_x
            //new -- for validation
            // val valid_x = valid_inputs >= x_th_x_reg 
            val valid_x = valid_inputs > x_th_x_reg * inputs_per_reg   
   
            Valid_In_Vec(sync_mem_idx)  := valid_x 
             
            // // mapping to 2D space -- compute logic -- 
            // val global_block_idx = sync_mem_idx.U * blocks_in_Sync_mem_2 + counter_2
            // val x_th_x_reg       = global_block_idx % total_blocks_per_y
            // val y_th_x_reg        = global_block_idx / total_blocks_per_y
            
            //Compute Logic 
            // val w_idx       = x_th_x_reg * x_elems_per_reg + cycleCount_2

            //Mux and shift logic 
            val w_idx = Mux(x_elems_per_reg === 1.U, x_th_x_reg, x_th_x_reg << 1) + cycleCount_2
            // val w_idx = (x_th_x_reg << x_shift) + cycleCount_2
            val linear_w    = w_idx + dirty_elems_w(dirty_elems_w_counter_1) // Mux(dirty_elems_w(buff_index) === 0.U, 0.U ,dirty_elems_w(buff_index) - 1.U)

            // //Computation loigc -- Version 1 
            // val W_reg_Index = linear_w / w_elems_per_reg
            // val w_offset    = linear_w % w_elems_per_reg 

            // ------------- Sync Read Memory System ------------------ // 
              Data_Vec(sync_mem_idx) := global_block_idx //RegNext( global_block_idx )
            // ------------- Sync Read Memory System ------------------ // 
            
            val W_reg_Index = Wire(UInt(linear_w.getWidth.W))
            val w_offset    = Wire(UInt(log2Ceil(4).W))
            val shift_amt   = Wire(UInt((w_offset.getWidth + 3).W)) // enough width

            when (w_elems_per_reg === 1.U) {
              W_reg_Index := linear_w
              w_offset    := 0.U
              shift_amt   := 0.U  // 8-bit weights
            } .elsewhen (w_elems_per_reg === 2.U) {
              W_reg_Index := linear_w >> 1
              w_offset    := linear_w(0)
              shift_amt   := linear_w(0) << 2  // 4-bit weights
            } .otherwise { // w_elems_per_reg === 4.U
              W_reg_Index := linear_w >> 2
              w_offset    := linear_w(1, 0)
              shift_amt   := linear_w(1, 0) << 1  // 2-bit weights
            }

            // // // Apply precomputed shift/mask -- version 4 W
            // val W_reg_Index = linear_w >> shift_amt_w
            // val w_offset    = linear_w & w_mask

            // ---------------------------------------------
            // 2. Extract Weight (W_reg) Value and Sign-Extend
            // ---------------------------------------------

            // //Compute 
            // val shift_amt = w_offset * weight_bits

            // Mux and shift 
            // val shift_amt = Wire(UInt((w_offset.getWidth + 3).W))
            // shift_amt := Mux( w_elems_per_reg === 4.U , w_offset << 1,   // 2-bit weights
            //             Mux(w_elems_per_reg === 2.U, w_offset << 2,   // 4-bit weights
            //                 w_offset << 3))    

            // val shift_amt   = w_offset << shift_amt_w_new   

            // val shifted   = W_reg(0)(W_reg_Index)(buff_index).asUInt  >> shift_amt //debug -- see 
            val shifted   = shift_amt
            
            // W_debug_2(sync_mem_idx) := RegNext(W_reg(0)(W_reg_Index)(buff_index).asUInt)


            //Stage 1  Outputs 
            xThXRegVec(sync_mem_idx) := RegNext(x_th_real) 
            // W_off_Vec(sync_mem_idx)  := w_offset
            yThXRegVec(sync_mem_idx) := RegNext(y_th_x_reg)
            shiftedVec(sync_mem_idx) := shifted
            WRegIdxdVec(sync_mem_idx) := W_reg_Index
          } 
          cycleCount_2_reg := cycleCount_2 



          // ------------------------------------------------------------------------------------------------> stage - 2  
  
          for (sync_mem_idx <- 0 until totalSyncMems) {

          // ------------- Sync Read Memory System ------------------ // 

                 //new new idea 
                 val W_reg_Index =  WRegIdxdVec(sync_mem_idx)

                //  //computation version
                // val wrapped = W_reg_Index % N.U 
                //  val bank_w       = wrapped / bankSize.U
                //  val byte_index = wrapped % bankSize.U

                 //optimized version (power of 2)
                //  val wrapped = W_reg_Index(log2Ceil(N)-1, 0)
                //  val bank_w     = wrapped >> 3       // wrapped / 8
                //  val byte_index = wrapped(2, 0)      // wrapped % 8, lower 3 bits

              //--------------------------------- new code 
              val S = bankSize.U
              val B = totalSyncMems.U

              val rows_used_per_bank =  (blocks_in_Sync_mem_1 + S - 1.U) / S // ceil(blocks_in_Sync_mem_1 / S)
              
              // x_th_x_reg is your linear element index
              val chunkIdx    = W_reg_Index / S
              val bank_w     = (chunkIdx / rows_used_per_bank) % B
              val row_in_bank = chunkIdx % rows_used_per_bank
              val byte_index  = W_reg_Index % S


                              // // group-per-bank
                              // val S = bankSize.U                 // elems per row
                              // val B = totalSyncMems.U               // banks
                              // val g = blocks_in_Sync_mem_1          // elems per bank (group)
              
                              // // x_th_x_reg is linear index
                              // val groupIdx    = W_reg_Index / g
                              // val posInBank   = W_reg_Index % g
                              // val bank_in     = groupIdx % B
                              // val row_in_bank = posInBank / S
                              // val byte_index  = posInBank % S
              //--------------------------------- new code 


                  // idea - 1 
                  // global_block_idx points to the base index
                  // val base_idx     = Data_Vec(sync_mem_idx)
                  // val global_block_idx = base_idx * x_elems_per_reg + cycleCount_2_reg
                  // val bank_w   = (global_block_idx / w_elems_per_chunk) % totalSyncMems.U
                  // val row_w    = global_block_idx / (totalSyncMems.U * w_elems_per_chunk)
                  // val offset_w = global_block_idx % w_elems_per_chunk
                  // // position inside the 64-bit word
                  // val offset_in_word = offset_w % w_elems_per_chunk 
                  // val byte_index     = offset_in_word 

                  
                // idea - 2 
                // val global_block_idx = Data_Vec(sync_mem_idx)
                // val bank_w = (global_block_idx / w_elems_per_chunk) % totalSyncMems.U
                // // val row_w = global_block_idx / (totalSyncMems * w_elems_per_chunk).U
                // val offset_w = global_block_idx % w_elems_per_chunk
                // val offset_in_word = global_block_idx % w_elems_per_chunk  // 0..elems_per_word-1
                // val byte_index = offset_in_word * weight_bits / 8.U  // 0..7
  
                val data_64 =  W_wire(buff_index)(bank_w) // W_vals(bank_w) 
                val bytes_8 = VecInit(Seq.tabulate(64 / params.WBitWidth)(i => data_64((i + 1) *  params.WBitWidth - 1, i * params.WBitWidth)))
                val W_value = bytes_8(byte_index)  
                // W_debug(sync_mem_idx) := W_value //debug -- see 
                val shift_amt   = shiftedVec(sync_mem_idx) 

            // ------------- Sync Read Memory System ------------------ // 

            //Input from previues stages  
            val shiftedReg =  W_value >> shift_amt 
            //  val shiftedReg =  shiftedVec(sync_mem_idx) //debug -- see 

            val weight_Sint_part = Wire(SInt(params.WBitWidth.W)) // adjust width as needed
            weight_Sint_part := 0.S  // default initialization to avoid uninitialized error

            when(weight_bits === 2.U) {
              val chunk2 = shiftedReg(1, 0)
              weight_Sint_part := Cat(Fill(6, chunk2(1)), chunk2).asSInt
            }.elsewhen(weight_bits === 4.U) {
              val chunk4 = shiftedReg(3, 0)
              weight_Sint_part := Cat(Fill(4, chunk4(3)), chunk4).asSInt
            }.elsewhen(weight_bits === 8.U) {
              val chunk8 = shiftedReg(7, 0)
              weight_Sint_part := Cat(chunk8(7), chunk8).asSInt
            }

            val abs_weight_uint        = Mux(weight_Sint_part < 0.S, -weight_Sint_part, weight_Sint_part).asUInt

            // ---------------------------------------------
            // 3. Calculate Row Index & Offset for Reading Bram 
            // ---------------------------------------------

            val row_in_block = Mux(abs_weight_uint(0) === 0.U, (abs_weight_uint >> 1) - 1.U, (abs_weight_uint - 1.U) >> 1)
            val row_idx      = start_row + row_in_block
            val elemOffset   = cycleCount_2_reg

            // ---------------------------------------------
            // 4. Extract Activation and Compute Product Sign
            // ---------------------------------------------

            val w_reg_valid_temp =  (abs_weight_uint =/= 0.U)  &&  Valid_In_Vec(sync_mem_idx) //  && valid_weights(buff_index) >  W_reg_Index//NEW

            //Stage 2  Outputs  (all used in next satge as input )
            weight_Sint_part_Reg(sync_mem_idx) := weight_Sint_part
            abs_weight_uint_Reg(sync_mem_idx)  := abs_weight_uint
            row_idx_Reg(sync_mem_idx)          := row_idx
            elemOffset_Reg(sync_mem_idx)       := elemOffset
            w_reg_valid_temp_Reg(sync_mem_idx) := w_reg_valid_temp
          }   
           
           read_on_delay := RegNext(READ_ON)
          // ------------------------------------------------------------------------------------------------> stage - 3 
          for (sync_mem_idx <- 0 until totalSyncMems) {

            //---------------- Input from previues stages  
             //From Stage 1 
            val  x_th_x_reg =  xThXRegVec(sync_mem_idx)
            val  y_th_x_reg =  yThXRegVec(sync_mem_idx)
          
            //From Stage 2 
            val weight_Sint_part = weight_Sint_part_Reg(sync_mem_idx) 
            val abs_weight_uint  = abs_weight_uint_Reg(sync_mem_idx)
            val row_idx          = row_idx_Reg(sync_mem_idx)   
            val elemOffset       = elemOffset_Reg(sync_mem_idx)  
            val w_reg_valid_temp = w_reg_valid_temp_Reg(sync_mem_idx) 


            // ------------- Sync Read Memory System ------------------ // 

            //--version 1 
            // val data_64 = X_vals(sync_mem_idx)
            // val bytes = VecInit(Seq.tabulate(64 / params.XBitWidth)(i => data_64((i + 1) *  params.XBitWidth - 1, i *  params.XBitWidth)))
            // val data = bytes(offset_counter_delay) 
            
            //--version 2
            //computation version
            // val wrapped     = x_th_x_reg % N_in.U 
            // val bank_in     = wrapped / bankSize_in.U
            // val byte_index  = wrapped % bankSize_in.U     


              //--------------------------------- new code 
              val S = bankSize_in.U
              val B = totalSyncMems.U

              val rows_used_per_bank =  (blocks_in_Sync_mem_1 + S - 1.U) / S // ceil(blocks_in_Sync_mem_1 / S)
              
              // x_th_x_reg is your linear element index
              val chunkIdx    = x_th_x_reg / S
              val bank_in     = (chunkIdx / rows_used_per_bank) % B
              val row_in_bank = chunkIdx % rows_used_per_bank
              val byte_index  = x_th_x_reg % S



                // // group-per-bank
                // val S = bankSize_in.U                 // elems per row
                // val B = totalSyncMems.U               // banks
                // val g = blocks_in_Sync_mem_1          // elems per bank (group)

                // // x_th_x_reg is linear index
                // val groupIdx    = x_th_x_reg / g
                // val posInBank   = x_th_x_reg % g
                // val bank_in     = groupIdx % B
                // val row_in_bank = posInBank / S
                // val byte_index  = posInBank % S
              //--------------------------------- new code 


            val data_64 =  X_vals(bank_in)
            val bytes = VecInit(Seq.tabulate(params.DMA_bits / params.XBitWidth)(i => data_64((i + 1) *  params.XBitWidth - 1, i *  params.XBitWidth)))
            val data  = bytes(byte_index)    
            // ------------- Sync Read Memory System ------------------ // 

            val extender = chunkExtenders(sync_mem_idx)(1)
            extender.io.data       := data  //X_reg(y_th_x_reg)(x_th_x_reg).asUInt
            extender.io.input_bits := input_bits
            extender.io.idx        := elemOffset
            val sign_X_reg_part = extender.io.out

            val sign_x       = sign_X_reg_part(input_bits - 1.U)
            val sign_w       = weight_Sint_part(weight_Sint_part.getWidth - 1)
            val product_sign = sign_x ^ sign_w // 0 => + | 1 => -

            // ---------------------------------------------
            // 5.  Compute Signed Product
            // ---------------------------------------------

            DATA_Y(sync_mem_idx) := RegNext(y_th_x_reg)

            val abs_val_new = Mux(sign_X_reg_part < 0.S, -sign_X_reg_part, sign_X_reg_part)
            val signed_val  = Mux(product_sign === 1.U, -abs_val_new, abs_val_new)

            val disable = (abs_weight_uint(0) === 0.U) || (abs_weight_uint === 0.U)  || !w_reg_valid_temp
            sum(sync_mem_idx) := RegNext(Mux(disable, 0.S, signed_val))

            // ---------------------------------------------
            // 6. Read Partial Product from Temp Memory
            // ---------------------------------------------

            val enable_read        = READ_ON && w_reg_valid_temp
            // val enable_read           = read_on_delay && w_reg_valid_temp

            val finalAddressInBRAM = row_idx

            Products(sync_mem_idx) := temp_muls_mem(sync_mem_idx).read(finalAddressInBRAM, enable_read) //Mux(enable_read, temp_muls_mem(sync_mem_idx).read(finalAddressInBRAM, enable_read),0.U)
            Sign(sync_mem_idx)     := RegNext(product_sign === 0.U)

            elemOffVec(sync_mem_idx)  := RegNext(elemOffset)
            w_reg_valid(sync_mem_idx) := RegNext(w_reg_valid_temp)
          }
          
          when(delay_counter === 1.U + 2.U  ){ 
            delay := false.B 
          }.elsewhen(delay_counter < 3.U) { 
            delay_counter := delay_counter + 1.U 
          }  

          when(READ_ON && !Done)  { 
            cycleCount_2 := Mux(cycleCount_2 === x_elems_per_reg - 1.U , 0.U ,cycleCount_2 + 1.U) 
            counter_2    := Mux(cycleCount_2 === x_elems_per_reg - 1.U ,counter_2 + 1.U,counter_2)
            Done         := Done || (cycleCount_2 === x_elems_per_reg - 1.U  && counter_2 === blocks_in_Sync_mem_2 - 1.U)
            // READ_ON      := !(cycleCount_2 === x_elems_per_reg - 1.U  && counter_2 === blocks_in_Sync_mem_2 - 1.U) //new 
          }

          // READ_ON := !Done  
          


          when(!delay && !Done_acc ) { // Accumulate values ( delay for first value only 1 cycle delay )  //&& stepCycle_2  === 0.U
            
            // ACCUMULATOR -- version 2 
            val groupSize = totalSyncMems / params.y_slice

            val accum = VecInit((0 until params.y_slice).map { y =>
              val offset_accum = y * groupSize

              // Explicitly typed to help Scala compiler with reduce
              val signedValues: Seq[SInt] = (0 until groupSize).map { value_idx =>
                val product_idx     = offset_accum + value_idx
                val offset          = elemOffVec(product_idx)
                val w_reg_valid_tmp = w_reg_valid(product_idx)
                
                val valid_read = Mux(
                  w_reg_valid_tmp,
                  Products(product_idx),
                  0.U(Products(product_idx).getWidth.W)
                )
                
                //computation 
                // val readDataVec_part = (valid_read >> (offset * chunks_per_Product_bits)) & mask_p
                
                //mux and shift 
                val readDataVec_part = Mux(offset === 0.U,
                valid_read & mask_p,
                (valid_read >> chunks_per_Product_bits) & mask_p
              )

                val unsigned_clean = Mux1H(Seq(
                  // (chunks_per_Product_bits ===  4.U)  -> readDataVec_part(3, 0),   // 2+2
                  // (chunks_per_Product_bits ===  6.U)  -> readDataVec_part(5, 0),   // 2+4 or 4+2
                  // (chunks_per_Product_bits ===  8.U)  -> readDataVec_part(7, 0),   // 4+4
                  (chunks_per_Product_bits === 10.U)  -> readDataVec_part(9, 0),   // 2+8 or 8+2
                  (chunks_per_Product_bits === 12.U)  -> readDataVec_part(11, 0),  // 4+8 or 8+4
                  (chunks_per_Product_bits === 16.U)  -> readDataVec_part(15, 0),  // 8+8
                  (chunks_per_Product_bits === 18.U)  -> readDataVec_part(17, 0),  // 16+2
                  (chunks_per_Product_bits === 20.U)  -> readDataVec_part(19, 0),  // 16+4
                  (chunks_per_Product_bits === 24.U)  -> readDataVec_part(23, 0)   // 16+8
                ))

          
                val readDataVec_part_sign = unsigned_clean.zext.asSInt

                // Signed accumulation with conditional sign adjustment
                val signedValue = Mux(Sign(product_idx), 
                                      readDataVec_part_sign - sum(product_idx), 
                                      -readDataVec_part_sign - sum(product_idx))


                signedValue
              }

              // Reduce with known SInt type
              signedValues.reduce(_ + _)
            }) 



          // // Recursive balanced adder tree function
          // def adderTree(values: Seq[SInt]): SInt = {
          //   if (values.length == 1) values.head
          //   else {
          //     val sums = values.grouped(2).map {
          //       case Seq(a, b) => a + b
          //       case Seq(a)    => a
          //     }.toSeq
          //     adderTree(sums)
          //   }
          // }

          // // Example usage for accum vector
          // val accum = VecInit((0 until params.y_slice).map { y =>
          //   val offset_accum = y * groupSize

          //   // Collect signed values
          //   val signedValues: Seq[SInt] = (0 until groupSize).map { value_idx =>
          //     val product_idx     = offset_accum + value_idx
          //     val offset          = elemOffVec(product_idx)
          //     val w_reg_valid_tmp = w_reg_valid(product_idx)

          //     val valid_read = Mux(
          //       w_reg_valid_tmp,
          //       Products(product_idx),
          //       0.U(Products(product_idx).getWidth.W)
          //     )

          //     val readDataVec_part = (valid_read >> (offset * chunks_per_Product_bits)) & mask_p

          //     val unsigned_clean = Mux1H(Seq(
          //       (chunks_per_Product_bits === 12.U) -> readDataVec_part(11, 0),
          //       (chunks_per_Product_bits === 16.U) -> readDataVec_part(15, 0),
          //       (chunks_per_Product_bits === 18.U) -> readDataVec_part(17, 0),
          //       (chunks_per_Product_bits === 20.U) -> readDataVec_part(19, 0),
          //       (chunks_per_Product_bits === 24.U) -> readDataVec_part(23, 0)
          //     ))

          //     val readDataVec_part_sign = unsigned_clean.zext.asSInt

          //     // Signed accumulation with conditional sign adjustment
          //     Mux(Sign(product_idx),
          //         readDataVec_part_sign - sum(product_idx),
          //         -readDataVec_part_sign - sum(product_idx))
          //   }

          //   // Use balanced adder tree for accumulation
          //   adderTree(signedValues)
          // })
     
            for (y <- 0 until params.y_slice) {
              O_reg(0)(y)(w_idx - 0.U) :=  (O_reg(0)(y)(w_idx - 0.U) +  accum(y))  //+xThXRegVec(y).asSInt // + X_DATA.asSInt + W_DATA.asSInt// RegNext(  ) //+ W_debug(0).asSInt + W_debug(1).asSInt + W_debug_2(0).asSInt + W_debug_2(1).asSInt
            }
            
            // Done_acc := RegNext(Done) 
            Done_acc := RegNext(RegNext(RegNext(Done))) //NEW_2

        }  


    }

  when(Done_acc && w_idx =/= cout_reg) { // select next Phase 
         
          Products.foreach(_ := 0.U) 
          cycleCount_2 := 0.U
          counter_2    := 0.U
          w_idx      := Mux(w_idx === cout_reg,0.U,w_idx +  params.w_slice.U)
          
          // ----------------- Sync Read Memory System ------------------ // 
          counter_row := 0.U 
          offset_counter := 0.U 

          row_weight := 0.U 
          elements_counter := 0.U 
          // ----------------- Sync Read Memory System ------------------ // 

          
          //new 
          READ_ON       := true.B
          read_on_delay := false.B  

          delay         := true.B
          delay_counter := 0.U

          // dirty_elems_w(dirty_elems_w_counter_1) := 0.U
          // dirty_elems_w_counter_1 := dirty_elems_w_counter_1 + 1.U 
          dirty_elems_w_counter_1 := Mux(dirty_elems_w_counter_1 === 2.U, 0.U, dirty_elems_w_counter_1 + 1.U)


          buff_elems(select_buff_idx)    := 0.U
          Done                           := false.B
          Done_acc                       := false.B
          buffers_mode(select_buff_idx)  := false.B
          select_buff_idx                := select_buff_idx + 1.U
          valid_weights(select_buff_idx) := 0.U 
          
    }.elsewhen(w_idx === cout_reg) { 

      when(load_x_cnt >= dma_limit) { 
                i_w        := 0.U
                i_w_start  := 0.U
                init_cycle    := true.B

                load_x_cnt := 0.U
                Rin_cnt    := 0.U
                state      := sStoreOutput
              }.otherwise{
                val XS_temp   = Mux(cin_reg - j_x  >= x_slice_input_reg,x_slice_input_reg,cin_reg - j_x )
                val mul1_temp = i_x * cin_reg
                val mul2_temp = XS_temp * x_elems_per_reg

                // i_w           := i_w_start + mul2_temp
                // i_w_start     := i_w_start + mul2_temp
                // --------------------------------------
                i_w           := i_w_start + step_new 
                i_w_start     := i_w_start + step_new 
                init_cycle    := true.B
                // --------------------------------------


                XS            := XS_temp
                // X_reg         := VecInit(Seq.fill(params.y_slice)(VecInit(Seq.fill(max_x_regs)(0.S(params.XBitWidth.W)))))
                dma_counter_a := 0.U
                dma_counter_d := 0.U
                mul2          := mul2_temp
                mul1          := mul1_temp

                //before load X
                val bit_offset_in_byte = ((mul1_temp + j_x) * input_bits) & (params.XBitWidth.U - 1.U) //( ((i_x * cin_reg) + j_x) * input_bits) %  params.XBitWidth.U
                val skipInFirstByte =bit_offset_in_byte / input_bits  //Mux(input_bits === 4.U, bit_offset_in_byte >> 2, bit_offset_in_byte >> 3)  
                // dirty_elems_x          := skipInFirstByte

                //++
                // val elems_per_word    = Mux(input_bits === 4.U, params.XBitWidth.U >> 2, params.XBitWidth.U >> 3)
                val elems_per_word = params.XBitWidth.U / input_bits
                all_available_elems_x := (XS * elems_per_word).min(cin_reg - j_x)  //( XS * (params.XBitWidth.U/input_bits )).min(cin_reg - j_x) 
                j_x_temp              := j_x

                //new
                chunkInfoModule_A.io.j_x  := j_x
                chunkInfoModule_A.io.mul1 := mul1_temp
                chunkInfoModule_A.io.mul2 := mul2_temp
                elements_to_read          := chunkInfoModule_A.io.elements_to_read
                chunk_addres              := chunkInfoModule_A.io.chunk_address

                //input
                chunkInfoModule_D.io.j_x_temp         := j_x
                chunkInfoModule_D.io.mul1             := mul1_temp
                chunkInfoModule_D.io.mul2             := mul2_temp
                chunkInfoModule_D.io.input_bits       := input_bits 
                chunkInfoModule_D.io.max_bits         := params.XBitWidth.U


                //output 
                bytes_to_read         :=  chunkInfoModule_D.io.bytes_to_read 
                reg_idx_start         :=  chunkInfoModule_D.io.reg_idx_start
                offset_in_chunk_1     :=  chunkInfoModule_D.io.offset_in_chunk_1
                elements_to_read_temp := chunkInfoModule_D.io.elements_to_read_temp

                state := sLoadX
                valid_inputs :=0.U


      }
          j_w              := 0.U
          w_idx            := 0.U
          cycleCount_2       := 0.U
          counter_2        := 0.U
          Done             := false.B
          Done_acc         := false.B
          buff_elems.foreach(_ := 0.U)
          lock             := false.B
         
      }

}

is(sStoreOutput) {
      if(params.DEBUG){  
        PC_storeO := PC_storeO + 1.U
      }

      val remaining_elems = cout_reg - dma_counter_d  // Elements left in the row
      val R_elems_per_chunk = ((elems_per_chunk.U - done_elems_in_block).min(remaining_elems)) 
      val linear_index  = i_o * cout_reg + j_o  
      val chunk_index   = linear_index / ((elems_per_chunk.U)) //.min(remaining_elems))   // Which chunk (64-bit block)
      val chunk_address = adr_O + (chunk_index * 8.U) 

      when(dma_send && !dma.io.busy && !lock) { //send request to DMA in A Channel 
        dma.io.mode := true.B   
      
      //NO Scale   
      val data_chunk = (0 until elems_per_chunk).foldLeft(0.U(64.W)) { (acc, i) =>
           acc | Mux( i.U < R_elems_per_chunk,
            O_reg(0)(Rin_cnt)(dma_counter_a + i.U).asUInt << ((i.U + done_elems_in_block) * params.OutBitWidth.U),
           0.U(64.W)
          )
      }


       //YES Scale   
      //  val scaleModule = Module(new Scale_Vector) 
      //  val dataVector = VecInit((0 until elems_per_chunk).map(i => Mux(i.U < R_elems_per_chunk, O_reg(0)(Rin_cnt)(dma_counter_a + i.U), 0.S(64.W))))
      //  scaleModule.io.inVector := dataVector 
      //  scaleModule.io.scale    := scale 
    
        // Option 2: Building the mask per byte lane (define beatBytes as total byte lanes in the beat)
        val beatBytes = 8  // Total number of byte lanes in a 64-bit word
        val bytesPerElem = params.OutBitWidth.U >> 3  // Divide by 8
        val startByte = done_elems_in_block * bytesPerElem
        val endByte   = startByte + (R_elems_per_chunk * bytesPerElem) //R_elems_per_chunk
        dma.io.mask := Cat((0 until beatBytes).map { i => (i.U >= startByte) && (i.U < endByte) }.reverse)

        // dma.io.writeData := scaleModule.io.out  // YES SCALE 
           dma.io.writeData :=  data_chunk        // NO Scale  

        dma.io.addr   := chunk_address
        dma.io.valid  := true.B
        dma_counter_a := dma_counter_a + R_elems_per_chunk
        dma_send      := dma_counter_a < cout_reg - R_elems_per_chunk
        dma.io.valid  := true.B

        for (i <- 0 until elems_per_chunk) {
          when(i.U < R_elems_per_chunk) {
              O_reg(0)(Rin_cnt)(dma_counter_a + i.U) := 0.S 
          }
        }

        lock    := true.B 
        j_o := j_o + R_elems_per_chunk 

      }

      when(dma.io.d_valid) { //response fron DMA in D channel   
        dma_counter_d := dma_counter_d + R_elems_per_chunk 
        dma_resp := dma_counter_d < cout_reg - R_elems_per_chunk 
        lock    := false.B 
        done_elems_in_block := Mux(done_elems_in_block === (elems_per_chunk.U - 1.U), 0.U, done_elems_in_block + R_elems_per_chunk)
      }
 
      when(!dma_send && !dma_resp && !(Rin_cnt === YS)) {  
        Rin_cnt       := Rin_cnt + 1.U
        dma_counter_d := 0.U
        dma_counter_a := 0.U
        j_o           := 0.U
        i_o           := i_o + 1.U
        dma_send      := true.B && (Rin_cnt < YS -1.U)
        dma_resp      := true.B && (Rin_cnt < YS -1.U)
      } 

      when(Rin_cnt === YS) {
         when(loops_cnt === LOOPS -1.U){
            Rin_cnt             := 0.U
            state               := sDELAY
            done_elems_in_block := 0.U
            loops_cnt := 0.U 

         }.otherwise{
            // === NEW/CHANGED RESET LOGIC ===
            // Reset and reinitialize the X-loading state when moving to the next loop (next set of Rin rows).
            // Without resetting the X-related counters/memory-read state the next Rin may use stale/empty X_vals,
            // producing zeros on subsequent Rin rows.
            loops_cnt     := loops_cnt + 1.U
            dma_send      := true.B
            dma_resp      := true.B
            dma_counter_a := 0.U
            dma_counter_d := 0.U

            // Reset Rin/X indexing so the next group of Rin rows reload X from memory afresh.
            Rin_cnt       := 0.U

            j_x           := 0.U
            j_x_start     := 0.U
            j_x_temp      := 0.U
            // reset the counters used by the sync-read state machine
            counter_row   := 0.U
            counter_mem   := 0.U
            offset_counter := 0.U
            cycleCount_1  := 0.U
            counter_1     := 0.U
            // clear Products & valid flags used during generation
            Products.foreach(_ := 0.U)
            valid_inputs := 0.U
            // clear X_vals to avoid using stale values in the transition cycle (helps simulation/debugging)
            for (i <- 0 until totalSyncMems) {
              X_vals(i) := 0.U
            }

            val XS_temp = Mux(cin_reg >= x_slice_input_reg,x_slice_input_reg,cin_reg)
            XS := XS_temp

            // new YS clamping
            val remain_in_column =  rin_reg - (i_x + 1.U)
            // YS :=  Mux(remain_in_column >= y_slice_Reg,y_slice_Reg,remain_in_column)

            // recompute chunk parameters for next X load
            val elems_per_word = params.XBitWidth.U / input_bits
            all_available_elems_x := (XS_temp * elems_per_word).min(cin_reg)

            // reset load counters
            load_x_cnt := 0.U

            // go to LoadX
            state := sLoadX
            valid_inputs := 0.U 

            val clamped        = Mux(cin_reg >= clmp, clmp, cin_reg) 


            val mul1_temp = i_x * cin_reg
            val mul2_temp = XS_temp * x_elems_per_reg 

            mul2          := mul2_temp
            mul1          := mul1_temp


                //new
                chunkInfoModule_A.io.j_x  := 0.U
                chunkInfoModule_A.io.mul1 := mul1_temp
                chunkInfoModule_A.io.mul2 := mul2_temp

                elements_to_read          := chunkInfoModule_A.io.elements_to_read
                chunk_addres              := chunkInfoModule_A.io.chunk_address

                //input
                chunkInfoModule_D.io.j_x_temp         := 0.U 
                chunkInfoModule_D.io.mul1             := mul1_temp
                chunkInfoModule_D.io.mul2             := mul2_temp
                chunkInfoModule_D.io.input_bits       := input_bits 
                chunkInfoModule_D.io.max_bits         := params.XBitWidth.U


                //output 
                bytes_to_read         :=  chunkInfoModule_D.io.bytes_to_read 
                reg_idx_start         :=  chunkInfoModule_D.io.reg_idx_start
                offset_in_chunk_1     :=  chunkInfoModule_D.io.offset_in_chunk_1
                elements_to_read_temp := chunkInfoModule_D.io.elements_to_read_temp

                // --- DIRTY INPUT ELEMENTS 
                val row_bits = cin_reg * input_bits * YS  // Total bits in one row
                val chunk_bits = params.DMA_bits.U  // Total bits in one chunk (e.g., 64 bits)

                // Calculate dirty bits in the last chunk
                val dirty_bits = row_bits % chunk_bits
                val dirty_elements = dirty_bits / input_bits
                dirty_elems_x := dirty_elements


                // val i = i_w - dma_counter_d 
                // val j = j_w 
                // val linear_index_dirty   = i  + j* cin_reg
                // val elems_per_chunk = params.DMA_bits.U / weight_bits
                // val skip_elems = linear_index_dirty % elems_per_chunk
                // dirty_elems_w(dirty_elems_w_counter_2) := skip_elems
                // --- DIRTY INPUT ELEMENTS 




         }

        }
      }
      is(sDELAY) {
          io.busy     := false.B  
          cmd.ready   := true.B 
        when(cmd.fire && (mode_6)) {
          state := sIdle 
        }

      }


      
          
      
  } 

  val counter_p = RegInit(0.U(5.W)) // Declare outside to preserve value

  if(params.DEBUG) {   
  when(cmd.fire && (mode_9)) {
      
        io.resp.valid := cmd.valid
        io.resp.bits.rd := cmd.bits.inst.rd          
   
        val mapping = Seq(
          0.U -> PC_loadX,
          1.U -> PC_Generate,
          2.U -> PC_loadW,
          3.U -> PC_Select,
          4.U -> PC_storeO,
          5.U -> PC_loadW_and_Select
        )

        io.resp.bits.data := MuxLookup(counter_p, 0.U)(mapping)


        counter_p := counter_p + 1.U
      } 
}

  when( ( buffers_mode.map(! _).reduce(_ || _)  && w_count =/= cout_reg ) && (state === sLoadW_and_sSelectAndAccumulate || state === sGenerateRegFiles)   ) { // Load W Phase 
      
      val buff_index = load_w_buff_idx 
      if(params.DEBUG){  
        PC_loadW := PC_loadW + 1.U 
      }  

      when(init_cycle){
        step_new := all_available_elems
        init_cycle := false.B
      } 
      
      when(dma_send && !dma.io.busy) { // send request to DMA in A Channel  
        
        val i_w_next = i_w + elements_to_read 

        dma.io.mode   := false.B  
        dma.io.addr   := chunk_addres 
        dma_counter_a := dma_counter_a + elements_to_read
        dma.io.valid  := true.B
        dma_send      := dma_counter_a < all_available_elems - elements_to_read 
        i_w           := i_w_next 
         
        //input 
        chunkInfoModule_A.io.j_x              := i_w_next
        chunkInfoModule_A.io.x_elems_per_reg  := w_elems_per_reg
        chunkInfoModule_A.io.elemsPerChunk_in := elemsPerChunk_w
        chunkInfoModule_A.io.adr_X            := adr_W
        chunkInfoModule_A.io.RegsPerChunk_in  := RegsPerChunk_w

        val dma_counter_a_next = dma_counter_a + elements_to_read

        //output
        // elements_to_read := min(chunkInfoModule_A.io.elements_to_read, all_available_elems - dma_counter_a)
        elements_to_read := Mux(chunkInfoModule_A.io.elements_to_read <= (all_available_elems - dma_counter_a_next), chunkInfoModule_A.io.elements_to_read, all_available_elems - dma_counter_a_next)
        chunk_addres     := chunkInfoModule_A.io.chunk_address
      }

      when(dma.io.d_valid) { // response from DMA in D channel   
        val i_w_temp_next      = i_w_temp + elements_to_read_temp
        val dma_counter_d_next = dma_counter_d + elements_to_read_temp
        // val dma_counter_d_next = (dma_counter_d + elements_to_read)(dma_counter_d.getWidth - 1, 0)

        val data  = dma.io.readData  // UInt(64.W)
        // val bytes = VecInit(Seq.tabulate(64 / params.WBitWidth)(i => data((i + 1) * params.WBitWidth - 1, i * params.WBitWidth).asSInt))
        
        // for (i <- 0 until 64 / params.WBitWidth) {
        //   when(i.U < bytes_to_read ) {
        //     W_reg(f_idx)(reg_idx_start + i.U)(buff_index) := bytes(offset_in_chunk_2 + i.U)
        //   }
        // }
        
        dma_counter_d := dma_counter_d_next
        dma_resp      := dma_counter_d < all_available_elems - elements_to_read_temp
        i_w_temp      := i_w_temp_next

        //input 
        chunkInfoModule_D.io.j_x_temp         := i_w_temp_next
        chunkInfoModule_D.io.x_elems_per_reg  := w_elems_per_reg
        chunkInfoModule_D.io.elemsPerChunk_in := elemsPerChunk_w
        chunkInfoModule_D.io.RegsPerChunk_in  := RegsPerChunk_w
        chunkInfoModule_D.io.dma_counter_d    := dma_counter_d_next 
        chunkInfoModule_D.io.input_bits       := weight_bits 
        chunkInfoModule_D.io.max_bits         := params.WBitWidth.U

        //output 
        bytes_to_read         :=  chunkInfoModule_D.io.bytes_to_read 
        reg_idx_start         :=  chunkInfoModule_D.io.reg_idx_start
        offset_in_chunk_2     := chunkInfoModule_D.io.offset_in_chunk_1
        // elements_to_read_temp := chunkInfoModule_D.io.elements_to_read_temp
        elements_to_read_temp := Mux(chunkInfoModule_D.io.elements_to_read_temp <= (all_available_elems - dma_counter_d_next), chunkInfoModule_D.io.elements_to_read_temp, all_available_elems - dma_counter_d_next)



        // ------------- Sync Read Memory System ------------------ // 
       valid_weights(buff_index) := valid_weights(buff_index) +  bytes_to_read //elements_to_read_temp 

      //  W_DATA := data 

        for (b <- 0 until buffers) {
          for (i <- 0 until totalSyncMems) {
            when (buff_index === b.U && (counter_mem_w === i.U)) {
              W_MEM(b)(i).write(counter_row_w, data)
            }
          }
        }

      when(counter_mem_w === totalSyncMems.U -1.U){ 
        counter_row_w := counter_row_w + 1.U
        counter_mem_w := 0.U 
      }.otherwise{
        counter_mem_w  := counter_mem_w + 1.U //1.U
      }

     // ------------- Sync Read Memory System ------------------ // 

    //   //------------------------------------------------------
    
    //   // scala
    //   val data64 = dma.io.readData
    //   val lanes  = VecInit(Seq.tabulate(S)(i => data64((i + 1) * params.WBitWidth - 1, i * params.WBitWidth)))

    //   val capInGroup = g_w - curElems_w
    //   val n0Total    = Mux(capInGroup <= S_U_w, capInGroup, S_U_w)   // to current bank (this beat)
    //   val n1         = S_U_w - n0Total                             // spill to next bank-

    //   // 1) place as many as fit in the current row
    //   val spaceInRow = S_U_w - curLaneCnt_w
    //   val n0a        = Mux(n0Total <= spaceInRow, n0Total, spaceInRow) // into current row

    //   val addCurA = (0 until S_w).foldLeft(0.U(params.DMA_bits.W)) { (acc, i) =>
    //     val take      = i.U < n0a
    //     val shiftBits = (curLaneCnt_w + i.U) * params.WBitWidth.U
    //     acc | Mux(take, (lanes(i.U) << shiftBits), 0.U)
    //   }
    //   val bufA   = curRowBuf_w | addCurA
    //   val laneA  = curLaneCnt_w + n0a
    //   val fullA  = laneA === S_U_w

    //   // 2) leftover lanes for same bank → start next row (combinational)
    //   val rem0 = n0Total - n0a
    //   val addCurB = (0 until S_w).foldLeft(0.U(params.DMA_bits.W)) { (acc, i) =>
    //     val iU        = i.U
    //     val take      = iU < rem0
    //     val shiftBits = iU * params.WBitWidth.U
    //     val srcIdx    = iU + n0a
    //     acc | Mux(take, (lanes(srcIdx) << shiftBits), 0.U)
    //   }

    //   // “after-this-beat” combinational view for current bank
    //   val bufAfter  = Mux(rem0 === 0.U, bufA, addCurB)
    //   val laneAfter = Mux(rem0 === 0.U, laneA, rem0)
    //   // if we filled rowA this cycle, the partial row (if any) lives in the next row index
    //   val rowToWriteAfter = Mux(fullA, curRowIdx_w + 1.U, curRowIdx_w)

    //   // 3) update regs for current bank row staging
    //   when (fullA) {
    //     // wrote rowA
    //     for (b <- 0 until buffers) { for (i <- 0 until totalSyncMems) { when (buff_index === b.U && (curBank_w === i.U)) { W_MEM(b)(i).write(curRowIdx_w, bufA) } } } 
    //     // for (i <- 0 until totalSyncMems) { when (curBank === i.U) { X_MEM(i).write(curRowIdx, bufA) } }
    //     curRowIdx_w := curRowIdx_w + 1.U
    //     // stage next row if rem0 > 0
    //     curRowBuf_w  := Mux(rem0 === 0.U, 0.U, addCurB)
    //     curLaneCnt_w := Mux(rem0 === 0.U, 0.U, rem0)
    //   } .otherwise {
    //     curRowBuf_w  := bufA
    //     curLaneCnt_w := laneA
    //   }

    //   // 4) group progress and flush on boundary using the after-this-beat wires
    //   val groupDone = (curElems_w + n0Total) === g
    //   curElems_w := Mux(groupDone, 0.U, curElems_w + n0Total)

    //   when (groupDone) {
    //     // flush the partial row of this bank (the “5th” element case)
    //     when (laneAfter =/= 0.U) {
    //       for (b <- 0 until buffers) { for (i <- 0 until totalSyncMems) { when (buff_index === b.U && (curBank_w === i.U)) { W_MEM(b)(i).write(rowToWriteAfter, bufAfter) } } } 
    //     }
    //       // clear staging and advance bank
    //       curRowBuf_w  := 0.U
    //       curLaneCnt_w := 0.U
    //       curBank_w    := Mux(curBank_w === (totalSyncMems - 1).U, 0.U, curBank_w + 1.U)
    //       curRowIdx_w  := 0.U
    //     }  

    //   // 5) spill to next bank (remaining lanes in this beat)
    //   when (n1 =/= 0.U) {
    //     val nextBank = Mux(groupDone, Mux(curBank_w === (totalSyncMems - 1).U, 0.U, curBank_w + 1.U), curBank_w)
    //     val addNext = (0 until S_w).foldLeft(0.U(params.DMA_bits.W)) { (acc, i) =>
    //       val iU        = i.U
    //       val inRange   = iU >= n0Total
    //       val relPos    = iU - n0Total
    //       val shiftBits = relPos * params.WBitWidth.U
    //       acc | Mux(inRange, (lanes(iU) << shiftBits), 0.U)
    //     }
    //     curBank_w    := nextBank
    //     curRowIdx_w  := Mux(groupDone, 0.U, curRowIdx)
    //     curRowBuf_w  := addNext
    //     curLaneCnt_w := n1
    //     curElems_w   := n1
    //   }
    // // //------------------------------------------------------
 
        
      }


      when(!dma_resp ) {  //!dma_resp && !dma_send //&& !dma_send

            // ------------- Sync Read Memory System ------------------ // 
            counter_row_w := 0.U 
            counter_mem_w := 0.U 
            counter_reg_w := 0.U 
            // ------------- Sync Read Memory System ------------------ // 

              i_w                      := i_w_start
              j_w                      := j_w + 1.U
              f_idx                    := 0.U
              dma_counter_a            := 0.U
              dma_send                 := true.B
              dma_resp                 := true.B
              lock                     := false.B
              buff_elems(buff_index)   := dma_counter_d
              dma_counter_d            := 0.U
              buffers_mode(buff_index) := true.B

              ///next W elements 
              val remain_in_column_regs = (cin_reg - i_w_start  + w_elems_per_reg -1.U)/w_elems_per_reg
              val XS_W_temp             = Mux(remain_in_column_regs >= x_slice_weights_reg,x_slice_weights_reg,remain_in_column_regs)
              XS_W := XS_W_temp 
              ///next W elements 

              load_w_buff_idx := load_w_buff_idx + 1.U //Mux( load_w_buff_idx === (buffers - 1).U ,load_w_buff_idx + 1.U,0.U)
              w_count         := w_count + 1.U
      
              //before load W
              val temp_mul1 = (j_w + 1.U) * cin_reg 
              val temp_mul2  = XS_W_temp * w_elems_per_reg 

              mul1 := temp_mul1 
              mul2 := temp_mul2

              val bit_offset_in_byte = (((temp_mul1) + i_w_start) * weight_bits) & (params.WBitWidth.U - 1.U)
              val skipInFirstByte    = bit_offset_in_byte / weight_bits 
              // dirty_elems_w(load_w_buff_idx + 1.U)     := skipInFirstByte //fix this  //load_w_buff_idx + 1.U
              val all_available_elems_next = (temp_mul2).min(cin_reg - i_w_start) 
              all_available_elems    := all_available_elems_next 
              i_w_temp               := i_w_start

             // -- Dirty Elemetns Weights Matrix 
             val i = i_w - dma_counter_d 
             val j = j_w 
             val linear_index_dirty   = i  + j* cin_reg
             val elems_per_chunk = params.DMA_bits.U / weight_bits
             val skip_elems = linear_index_dirty % elems_per_chunk
             dirty_elems_w(dirty_elems_w_counter_2) := skip_elems
             dirty_elems_w_counter_2 := Mux(dirty_elems_w_counter_2 === 2.U, 0.U, dirty_elems_w_counter_2 + 1.U)
             // -- Dirty Elemetns Weights Matrix 

              chunkInfoModule_A.io.j_x              := i_w_start 
              chunkInfoModule_A.io.x_elems_per_reg  := w_elems_per_reg
              chunkInfoModule_A.io.elemsPerChunk_in := elemsPerChunk_w
              chunkInfoModule_A.io.adr_X            := adr_W
              chunkInfoModule_A.io.RegsPerChunk_in  := RegsPerChunk_w
              chunkInfoModule_A.io.mul1             := temp_mul1
              chunkInfoModule_A.io.mul2             := temp_mul2

              // elements_to_read          := chunkInfoModule_A.io.elements_to_read
              elements_to_read := Mux(chunkInfoModule_A.io.elements_to_read <= all_available_elems_next, chunkInfoModule_A.io.elements_to_read, all_available_elems_next)
              chunk_addres              := chunkInfoModule_A.io.chunk_address

              //input 
              chunkInfoModule_D.io.j_x_temp         := i_w_start 
              chunkInfoModule_D.io.x_elems_per_reg  := w_elems_per_reg
              chunkInfoModule_D.io.elemsPerChunk_in := elemsPerChunk_w
              chunkInfoModule_D.io.RegsPerChunk_in  := RegsPerChunk_w
              chunkInfoModule_D.io.mul1             := temp_mul1
              chunkInfoModule_D.io.mul2             := temp_mul2
              chunkInfoModule_D.io.input_bits       := weight_bits 
              chunkInfoModule_D.io.max_bits         := params.WBitWidth.U


              //output 
              bytes_to_read         := chunkInfoModule_D.io.bytes_to_read
              reg_idx_start         := chunkInfoModule_D.io.reg_idx_start
              offset_in_chunk_2     := chunkInfoModule_D.io.offset_in_chunk_1
              // elements_to_read_temp := chunkInfoModule_D.io.elements_to_read_temp
              elements_to_read_temp := Mux(chunkInfoModule_D.io.elements_to_read_temp <= all_available_elems_next , chunkInfoModule_D.io.elements_to_read_temp, all_available_elems_next)


            } 
  }

}

   

class WithDataReuseAccelerator extends Config((site, here, up) => {
  case BuildRoCC => Seq(
    (p: Parameters) => {
      implicit val implicitParams: Parameters = p
      implicit val valName: ValName = ValName("MatMul_Data_Reuse_example")

      // Pass fbus to DataReuseExample
      LazyModule(
        new DataReuseExample(
          opcodes = OpcodeSet.all,            // Opcode used for the accelerator
          params  = Data_Reuse_Config.Data_Reuse_Config  // Use the config from the LinearFilterConfig object
        ) {
  
        }
      )
    }
  )
})