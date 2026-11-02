///////////////////////////////////////////////////////////////////////////////////////////////////////////////
package LUMAX_PACKAGE

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

// my imports
import LUMAX_PACKAGE.ChunkUtils._   // import your function from the object
import LUMAX_PACKAGE.LUMAX_Config

class LUMAXExample(opcodes: OpcodeSet, val params: LUMAXParams)
  (implicit p: Parameters) extends LazyRoCC(opcodes = opcodes) {

  override lazy val module = new LUMAXExampleModuleImpl(this)

  val dma = LazyModule(new DmaModule(LUMAX_Config.LUMAX_Config))

  tlNode   := dma.node  // Connect the memory module to the accelerator’s TileLink node

}

// File map (all hardware state and assignment order are preserved):
//   1. Parameters, command decode, storage and register groups.
//   2. Submodule instances, default connections and shared selection cursors.
//   3. The six-state controller, kept together in execution order.
//   4. Counter readback and weight prefetch that run alongside the controller.
// Packing and DMA helper method bodies live in LUMAXTopHelpers.scala.

class LUMAXExampleModuleImpl(outer: LUMAXExample)(implicit p: Parameters) extends LazyRoCCModuleImp(outer) with HasCoreParameters with LUMAXTopHelpers {

  // === Parameters and elaboration requirements ===
  val params: LUMAXParams = outer.params
  require(params.y_slice >= 1 && params.Rin >= params.y_slice,
    "Rin row capacity must be at least y_slice")
  if (params.FP16_ACTIVATIONS) {
    require(params.XBitWidth == 16 && params.OutBitWidth == 32 && !params.SCALE,
      "FP16 activations require XBitWidth=16, OutBitWidth=32 and SCALE=false")
    require(params.Cin >= 1 && params.Cin <= 65536 && params.WBitWidth == 8,
      "FP16 supports Cin<=65536 and signed W8/W4/W2")
  }
  // Maximum finite FP16 * signed W8 in units of 2^-24 is strictly below 2^47.
  val accumulatorWidth = if (params.FP16_ACTIVATIONS) 48 + log2Ceil(params.Cin) else params.OutBitWidth
  val dma = outer.dma.module

  // === Command interface and instruction decode ===
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
  val mode_10        = funct === 10.U // Start Calculation

  // === Memory geometry ===
  //SyncMem compile parameters
  val RowsPerBlock      = (1 << (params.WBitWidth - params.minWeightBits)) * params.Mem_row_factor // How many Rows have one SynMem
  val totalSyncMems     =  (params.x_slice * params.y_slice)      //How  many SyncMem the design need to worst case
  val weightBanks = params.x_slice // Shared by all activation-row groups.
  val maxParts          = params.XBitWidth / params.minInputBits   // at Worst case one X_reg / SynMem have splitted have maxPart differentt elemtns
  val product_bitwidth  = (params.XBitWidth/params.minInputBits)*(params.WBitWidth + params.minInputBits)

  // === Completion and selection-control registers ===
  val done          = RegInit(false.B)
  val Done          = RegInit(false.B)
  val Done_acc      = RegInit(false.B)
  val delay         = RegInit(false.B)
  val delay_counter = RegInit(0.U(3.W))  // Enough to count up to 2

  val sum    = Reg(Vec(totalSyncMems, SInt((params.XBitWidth + 1).W)))  // sum in Select Products and Accumulation
  val DATA_Y = Reg(Vec(totalSyncMems, UInt(product_bitwidth.W)))                   // Store read results
  val Sign   = Reg(Vec(totalSyncMems, Bool()))                                     // Store the sign as a boolean (true/false) for each BRAM
  val Products = RegInit(VecInit(Seq.fill(totalSyncMems)(0.U(product_bitwidth.W)))) // Store read results

  // === Scratchpad capacity constants ===
  //How many X_reg will neeed in worst case
  val regs_per_mem = RowsPerBlock
  val regs_per_mem_w = maxParts * regs_per_mem
  val max_x_regs  = params.x_slice * regs_per_mem //same as total blocks (one X_reg at worst case for every SynMem)
  val max_w_elems = maxParts* max_x_regs              // How many W registers we will neeed in worst case to bring all corresping elemetns (speedup)
  val buffers     =  params.W_BUFFS
  val max_blocks_in_sync_mem = regs_per_mem

  // === Output accumulators and activation/weight memories ===
  // Create Scratchpad Memory
  val O_reg     = RegInit(VecInit(Seq.fill(params.num_filters)(VecInit(Seq.fill(params.y_slice)(VecInit(Seq.fill(params.Cout)(0.S(accumulatorWidth.W))))))))  // Register for Output results
  val fp16Meta = if (params.FP16_ACTIVATIONS) Some(Reg(Vec(totalSyncMems, new FP16ProductMeta))) else None
  val fp16Flags = if (params.FP16_ACTIVATIONS) Some(RegInit(VecInit(Seq.fill(params.y_slice)(VecInit(Seq.fill(params.Cout)(0.U(3.W))))))) else None
  val I_MEM = Seq.fill(totalSyncMems)(SyncReadMem(regs_per_mem/4, UInt(params.DMA_bits.W)))
  val W_MEM = Seq.fill(buffers, weightBanks) { SyncReadMem(regs_per_mem_w/8, UInt(params.DMA_bits.W))}

  // === DMA response carry and split bookkeeping ===
  // ------------- Sync Read Memory System ----- VERSION 2 ------------- //
  val dataResponse = RegInit(0.U(params.DMA_bits.W))
  val usedResponse = RegInit(0.U(log2Up(2*params.DMA_bits + 1).W))
  val currentBank = RegInit(0.U(1.W))
  val storedInBlock = RegInit(0.U(log2Up(RowsPerBlock + 1).W))

  val dataResponse_w = RegInit(0.U(params.DMA_bits.W))
  val usedResponse_w = RegInit(0.U(log2Up(2*params.DMA_bits + 1).W))
  val currentBank_w = RegInit(0.U(1.W))
  val storedInBlock_w = RegInit(0.U(log2Up(RowsPerBlock + 1).W))

  val SplitEnable        = RegInit(false.B) //sending DMA requests to A channel on progress
  val SplitCounter       = RegInit(0.U(log2Ceil( totalSyncMems + 1).W))
  // ------------- Sync Read Memory System ----- VERSION 2 ------------- //

  // === Activation scratchpad read registers ===
  // counter register to iterate rows -- for I_MEM
  val counter_row = RegInit(0.U(log2Ceil(max_blocks_in_sync_mem).W))
  val counter_mem = RegInit(0.U(log2Ceil(totalSyncMems).W))
  val offset_counter = RegInit(0.U(log2Ceil(4).W))
  val I_vals = RegInit(VecInit(Seq.fill(totalSyncMems)(0.U(params.DMA_bits.W)))) // Store read results

  // === Weight scratchpad read and validity registers ===
  // counter register to iterate rows -- for W_MEM
  val counter_row_w = RegInit(0.U(log2Ceil(max_blocks_in_sync_mem).W))
  val counter_mem_w = RegInit(0.U(log2Ceil(totalSyncMems).W))

  // val valid_inputs  = Reg(UInt((totalSyncMems*max_blocks_in_sync_mem).W))
  val valid_inputs = Reg(UInt(log2Ceil(totalSyncMems * max_blocks_in_sync_mem + 1).W))
  val valid_weights = Reg(Vec(buffers, UInt((log2Ceil(totalSyncMems * max_blocks_in_sync_mem + 1).W))))

  val row_weight = RegInit(0.U(log2Ceil(max_blocks_in_sync_mem + 1).W))
  val elements_counter = RegInit(0.U(log2Ceil(totalSyncMems * max_blocks_in_sync_mem + 1).W))

  // === Weight double-buffer registers ===
  //Double Bufferig Parameters
  val buffers_mode  = RegInit(VecInit(Seq.fill(buffers)(false.B)))  // 0-> load W ,  1 -> SelectandAccumulate
  // val buff_elems    = RegInit(VecInit(Seq.fill(buffers)(0.U(log2Ceil(math.max(max_x_regs, params.Cout) + 1).W))))
  val dirty_elems_w = RegInit(VecInit(Seq.fill(buffers + 1)(0.U(log2Ceil(params.DMA_bits / params.minWeightBits).W))))
  val dirty_elems_w_counter_1  = RegInit(0.U(log2Ceil(buffers+1).W))
  val dirty_elems_w_counter_2  = RegInit(0.U(log2Ceil(buffers+1).W))
  val load_w_buff_idx  = RegInit(0.U(log2Ceil(buffers).W))
  val select_buff_idx  = RegInit(0.U(log2Ceil(buffers).W))
  val w_count          = RegInit(0.U(log2Ceil(params.Cout + 1).W)) // counter to for current Weight matrix Column

  // === Product-table storage and generation/select counters ===
  //Create SyncReadMems (Memory Blocks to store Prodcuts)
  val temp_muls_mem = Seq.fill(totalSyncMems) {SyncReadMem(RowsPerBlock  , UInt(product_bitwidth.W))} //FIX THIS
  val cycleCount_1    = RegInit(0.U(log2Ceil(64 + 1).W)) // Track the current row inside a activation block
  val cycleCount_2    = RegInit(0.U(log2Ceil(64 + 1).W)) // Track the current block to write //blocksPerBRAM
  val cycleCount_2_reg    = RegInit(0.U(log2Ceil(64 + 1).W)) // Track the current block to write //blocksPerBRAM

  // === Output-column and row-group progress ===
  // Index counters
  val w_idx         = RegInit(0.U(log2Ceil(params.Cout + 1).W)) // counter to for current Weight matrix Column
  val f_idx         = RegInit(0.U(log2Ceil(params.num_filters  + 1).W)) //counter to track current Weight fildter (NOT USED)
  val load_x_cnt    = RegInit(0.U(64.W)) // track the cuurenct x_slice have been loaded [1,Cin/x_slice]
  val Rin_cnt       = RegInit(0.U(log2Ceil(params.y_slice + 1).W)) //track the current Rin that processed [1,parallel_Rin]
  val loops_cnt     = RegInit(0.U(64.W))

  // === Matrix-coordinate registers ===
  //Indexer for Matrices
  val i_w           = RegInit(0.U(log2Ceil(params.Cin + 1).W))
  val j_w           = RegInit(0.U(log2Ceil(params.Cout + 1).W))
  val i_x           = RegInit(0.U(log2Ceil(params.Cin.max(params.Rin) + 1).W))
  val j_x           = RegInit(0.U(log2Ceil(params.Cin + 1).W))
  val i_w_start     = RegInit(0.U((log2Ceil(params.Cin + 1).W)))
  val i_x_start     = RegInit(0.U(log2Ceil(params.Cin.max(params.Rin) + 1).W))
  val j_x_start     = RegInit(0.U(log2Ceil(params.Cin + 1).W)) // Activation K-block offset.
  val LOOPS         = RegInit(0.U(64.W))
  val i_o           = RegInit(0.U(log2Ceil(params.Rin + 1).W))
  val j_o           = RegInit(0.U(log2Ceil(params.Cout + 1).W))
  val j_x_temp      = RegInit(0.U(log2Ceil(params.Cin + 1).W))
  val i_w_temp      = RegInit(0.U(log2Ceil(params.Cin + 1).W))

  val init_cycle      = RegInit(true.B)
  val step_new        = RegInit(0.U(log2Ceil(params.Cin + 1).W))

  // Register to keep loops limit at runtime ChunkSignExtender
  val XS            =  RegInit(max_x_regs.U(log2Ceil(max_x_regs + 1).W))
  val XS_W          =  RegInit(max_w_elems.U(log2Ceil(max_w_elems + 1).W))
  val YS            =  RegInit(params.y_slice.U(log2Ceil(params.y_slice + 1).W))
  val elems_per_chunk        = (params.DMA_bits/params.OutBitWidth)
  val done_elems_in_block    =  RegInit(0.U(log2Ceil(elems_per_chunk).W))

  //Produce register files for method -2 only
  val counter_1 = RegInit(0.U(log2Ceil(RowsPerBlock + 2).W))  // Counter to track how many Register Files entries are filled
  val counter_2 = RegInit(0.U(log2Ceil(RowsPerBlock + 2).W))  // Counter to track how many Register Files entries are filled

  //Add registers to track the ongoing DMA transaction's mode
  val dma_send        = RegInit(false.B) //sending DMA requests to A channel on progress
  val dma_resp        = RegInit(false.B) //receiveving  DMA responces from  D channel on progress

  val dma_counter_a   = RegInit(0.U(log2Ceil(math.max(max_x_regs, params.Cout) + 1).W))
  val dma_counter_d   = RegInit(0.U(log2Ceil(math.max(max_x_regs, params.Cout) + 1).W))

  //Select and Accumulte control Signals
  val lock            = RegInit(false.B) //send one DMA request to A channel and wait the responce from D channel (muitex)
  val READ_ON         = RegInit(false.B)
  val read_on_delay   = RegInit(false.B)

  // Matrices Addresses
  val adr_X      =  RegInit(0.U(64.W))
  val adr_W      =  RegInit(0.U(64.W))
  val adr_O      =  RegInit(0.U(64.W))

  // === Runtime matrix dimensions and slice settings ===
  //Register to save dimension values
  val dma_limit             = RegInit(0.U(log2Ceil(params.Cin + 1).W)) //Cin/X_slice
  val cout_reg              = RegInit(params.Cout.U(log2Ceil(params.Cout + 1).W))
  val cin_reg               = RegInit(params.Cin.U(log2Ceil(params.Cin + 1).W))
  val rin_reg               = RegInit(params.Rin.U(log2Ceil(params.Rin + 1).W))
  val x_slice_Reg           = RegInit(params.x_slice.U(log2Ceil(params.x_slice + 1).W))
  val y_slice_Reg           = RegInit(params.y_slice.U(log2Ceil(params.y_slice + 1).W))
  val x_slice_weights_reg   = RegInit(max_w_elems.U(log2Ceil(max_w_elems + 1).W)) // to bring all corresping weights for all x_elemnts
  val x_slice_input_reg     = RegInit(max_x_regs.U(log2Ceil(max_x_regs + 1).W)) // so in worst case every sync Mem have hiw own X_reg
  val scale = RegInit(0.U(32.W)) // 32-bit register for IEEE 754 float

  // === Runtime element formats and performance counters ===
  //bitwidths
  val input_bits  = RegInit(0.U(6.W)) //  input bits
  val weight_bits = RegInit(0.U(6.W)) //  weights bits
  val output_bits = RegInit(0.U(6.W)) //  output bits

  val PC_loadX    =  RegInit(0.U(64.W))
  val PC_Generate =  RegInit(0.U(64.W))
  val PC_loadW    =  RegInit(0.U(64.W))
  val PC_Select   =  RegInit(0.U(64.W))
  val PC_storeO   =  RegInit(0.U(64.W))
  val PC_loadW_and_Select = RegInit(0.U(64.W))

  // === Activation validity and packed-element metadata ===
  //Dynamic Input Paramters  characteristcs for every w element
  val all_available_elems   = RegInit(0.U(log2Ceil(params.Cin + 1).W))                        // How many elements are available to read in current block (for both W and X since they are the same in worst case)
  val elemOffVec            = Reg(Vec(totalSyncMems, UInt(log2Ceil(maxParts).W)))             // Keep in what offset ot row is located the element weight want to read
  val w_reg_valid           = Reg(Vec(totalSyncMems, Bool()))                                 // true -> selected value from BRAM is valid
  val dirty_elems_x         = RegInit(0.U(log2Ceil(params.DMA_bits/params.minInputBits).W))   // How many dirty elements we have in current X_reg (dirty -> the element is already used and we need to bring new one to replace it)
  // Counts activation elements along K; output-column capacity must not truncate it.
  val all_available_elems_x = RegInit(0.U(log2Ceil(params.Cin + 1).W))
  val x_elems_per_reg       = RegInit((params.XBitWidth / params.minInputBits).U)             //How many input elements fit  in one input register
  val readDataVec_part            = Reg(Vec(totalSyncMems, UInt(log2Ceil(maxParts).W)))       // Keep in what offset ot row is located the element weight want to read
  val w_elems_per_reg = RegInit((params.WBitWidth / params.minWeightBits).U(3.W) )            //How many weight elements fit  in one weight register

  // === Product-table geometry and precision masks ===
  //Dynamic Bitwidth
  val mask_in              = RegInit(0.U(log2Ceil(((1 << params.XBitWidth) - 1) + 1).W))
  val mask_w               = RegInit(0.U(log2Ceil(((1 << params.WBitWidth) - 1) + 1).W))
  val mask_p               = RegInit(0.U(log2Ceil(((1 << (params.WBitWidth + params.XBitWidth)) - 1) + 1).W))
  val block_rows           = RegInit(0.U(log2Ceil((1 << (params.WBitWidth - 2)) + 1).W))
  val blocks_in_Sync_mem = RegInit(0.U(log2Ceil((max_blocks_in_sync_mem) + 1).W)) //new
  val shiftAmt  = Log2(block_rows)  //  block_rows = {1,2,64}
  val chunks_per_Product_bits =  RegInit(0.U(log2Ceil((((params.WBitWidth +  params.XBitWidth) ))  + 1).W))

  // === DMA request/response coordinates and chunk sizes ===
  //indexing extra registers
  val mul1 = RegInit(0.U(matrixIndexWidth(params).W))
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

  // DMA bus parameters (how many register or elements fitr in 64 bits dma channel)
  val elemsPerChunk_in = RegInit(0.U(5.W))  // holds up to 16
  val RegsPerChunk_in  = RegInit((params.DMA_bits / params.XBitWidth).U(math.max(1, log2Ceil(params.DMA_bits / params.XBitWidth) + 1).W))
  val elemsPerChunk_w  = RegInit((params.DMA_bits / params.minInputBits).asUInt)  // and give it 6.W width
  val RegsPerChunk_w   = RegInit((params.DMA_bits / params.WBitWidth).U(math.max(1, log2Ceil(params.DMA_bits / params.WBitWidth) + 1).W))
  val  clmp = RegInit(0.U(64.W))  // holds up to 16

  val max_val = 16 * params.x_slice  // maybe 64 here ? TODO
  val mems_per_ys = params.x_slice
  val total_blocks_per_y =  params.x_slice.U * blocks_in_Sync_mem

  // === Selection pipeline registers and shared address counters ===
  //----------------------------->  Pipeline Stages in Select Phase  Registers

  // stage 1 Output Registers
  // Activation selection previously used two registers of global indices.
  // Keep that latency, but carry one shared cell and bank rotation instead.
  val activationCellS3 = RegInit(0.U(math.max(1, log2Ceil(params.DMA_bits / params.XBitWidth)).W))
  val activationBankShiftS3 = RegInit(0.U(math.max(1, log2Ceil(totalSyncMems)).W))
  val yThXRegVec = Reg(Vec(totalSyncMems, UInt(log2Ceil(params.y_slice).W)))
  val shiftedVec = Reg(Vec(totalSyncMems, UInt(params.WBitWidth.W)))

  val N =  (params.DMA_bits/params.WBitWidth)*totalSyncMems
  val bankSize = params.DMA_bits/params.WBitWidth

  val N_in = (params.DMA_bits/params.XBitWidth)*totalSyncMems
  val bankSize_in = params.DMA_bits/params.XBitWidth

  val bankIdxW     = log2Ceil(totalSyncMems)
  val wCellIdxW    = log2Ceil(params.DMA_bits / params.WBitWidth)
  val xCellIdxW    = log2Ceil(params.DMA_bits / params.XBitWidth)
  val buffIdxW     = log2Ceil(buffers)
  val chunkPartIdx = if (maxParts > 1) 1 else 0

  val weightCellS2 = RegInit(0.U(math.max(1, wCellIdxW).W))
  val weightOffsetS2 = RegInit(0.U(math.max(1, log2Ceil(params.WBitWidth / params.minWeightBits)).W))
  val Valid_In_Vec = Reg(Vec(totalSyncMems, Bool()))

  // stage 2 Output Registers
  val weight_Sint_part_Reg = Reg(Vec(weightBanks, SInt(params.WBitWidth.W)))
  val abs_weight_uint_Reg  = Reg(Vec(weightBanks, UInt(params.WBitWidth.W)))
  val row_idx_Reg          = Reg(Vec(weightBanks, UInt(log2Ceil(RowsPerBlock + 1).W)))
  val elemOffset_Reg       = Reg(Vec(weightBanks, UInt(4.W)))
  val w_reg_valid_temp_Reg = Reg(Vec(weightBanks, Bool()))

  val offsetWidth = log2Ceil(params.WBitWidth / params.minWeightBits)
  val cellWidth   = log2Ceil(params.DMA_bits / params.WBitWidth)

  // All weight banks consume the same local K position together. Count the
  // packed element first (W8: 0; W4: 0..1; W2: 0..3), then the byte (0..7).
  // These counters hold during stalls/drain and restart for every column.
  val offset_counter_1 = RegInit(0.U(math.max(1, offsetWidth).W))
  val cell_counter = RegInit(0.U(math.max(1, cellWidth).W))
  val lastWeightOffset = Mux(weight_bits === 8.U, 0.U,
    Mux(weight_bits === 4.U, 1.U, 3.U))

  // One activation cursor serves both mutually exclusive phases: generate
  // LUT products, then replay activations for every output column. Its bank
  // rotation also preserves legacy dirty offsets across short bank blocks.
  val activationAddress = Module(new ActivationAddressCounter(
    totalSyncMems, bankSize_in, max_blocks_in_sync_mem,
    params.XBitWidth / params.minInputBits,
    params.DMA_bits / params.minInputBits - 1))
  activationAddress.io.restart := false.B
  activationAddress.io.advance := false.B
  activationAddress.io.blocks := blocks_in_Sync_mem
  activationAddress.io.elementsPerCell := x_elems_per_reg
  activationAddress.io.startOffset := dirty_elems_x

  // Helper implementations are in LUMAXTopHelpers.scala.

  // === Submodule instances and default connections ===
  //////////////////////////////////////////////////////////////// Modules ////////////////////////////////////////////////////////

  // === DMA interface defaults ===
  //DMA Module inputs
  dma.io.valid     := false.B
  dma.io.addr      := 0.U
  dma.io.mode      := 3.U      // inactive
  dma.io.writeData := 0.U
  dma.io.log_S     := log2Ceil(params.DMA_Bytes).U
  dma.io.mask      := 0.U
  dma.io.d_ready   := false.B //not used

  // === Activation chunk extenders ===
  // ChunkSignExtender Module
  val chunkExtenders = Seq.fill(totalSyncMems,maxParts) { Module(new ChunkSignExtender(params.XBitWidth,params.minInputBits))}
  for {
    i <- 0 until totalSyncMems
    j <- 0 until maxParts
  } {
    val ext = chunkExtenders(i)(j)
    ext.io.data       := 0.U
    ext.io.input_bits := 0.U
    ext.io.idx        := 0.U
  }

  // === Parallel product generators (b times Y_SLICE) ===
  // Product Generator Mesh Module
  val Product_Generator_Mesh = Seq.fill(totalSyncMems) {
    Module(new Product_Generator(
      totalSyncMems,
      maxParts,
      params.WBitWidth,
      params.XBitWidth,
      params.minWeightBits,
      RowsPerBlock,
      params.DMA_bits,
      product_bitwidth,
      params.minInputBits,
      params.FP16_ACTIVATIONS,
      if (params.y_slice > 1) weightBanks else 0
    ))
  }

  for (i <- 0 until totalSyncMems) {
    val gen = Product_Generator_Mesh(i)

    // Give VALID default values
    gen.io.sync_mem_idx := i.U
    gen.io.blocks_in_Sync_mem := blocks_in_Sync_mem
    gen.io.counter := counter_1
    gen.io.dirty_elems_x := dirty_elems_x
    gen.io.activation_bank := rotatedActivationBank(i, activationAddress.io.bankShift)
    gen.io.activation_cell := activationAddress.io.cell
    gen.io.X_vals := I_vals
    gen.io.inputs_per_reg := x_elems_per_reg
    gen.io.input_bits := input_bits
    gen.io.valid_inputs := valid_inputs
    gen.io.mask_p := mask_p
    gen.io.shiftAmt := shiftAmt
    gen.io.chunks_per_Product_bits := chunks_per_Product_bits
    gen.io.Product_in := Products(i)
  }

  // === DMA address/chunk-info modules ===
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

  // === Main controller: six states ===
  //////////////////////////////////////////// FSM Implementation ////////////////////////////////////////////////////////

  // Define states for the FSM
  val sIdle :: sLoadX :: sGenerateRegFiles :: sLoadW_and_sSelectAndAccumulate :: sStoreOutput :: sDELAY :: Nil = Enum(6)
  val state = RegInit(sIdle)

  //If cmd.ready is hardcoded to true.B, the CPU ignores io.busy  (???)
  io.busy     := (state =/= sIdle)
  cmd.ready   := (state === sIdle)

  switch(state) {
    // === State 1: accept configuration and start a matmul ===
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
        offset_counter_1 := 0.U
        cell_counter := 0.U
        if (params.y_slice > 1) dirty_elems_x := 0.U
        valid_weights := VecInit(Seq.fill(buffers)(0.U((totalSyncMems*params.DMA_bits/params.WBitWidth).W)))

        // XS YS Registers Init
        YS      := Mux(rin_reg >= y_slice_Reg,y_slice_Reg,rin_reg)
        elemsPerChunk_in :=   params.DMA_bits.U  / input_bits
        elemsPerChunk_w  :=   params.DMA_bits.U  / weight_bits
      }

      when(cmd.fire && (mode_1)) {
        adr_W     := rs1

        val scaledSlice        = rs2(15, 0)   // 16 bits
        x_slice_weights_reg   := rs2(31, 16)  // 16 bits
        dma_limit             := rs2(47, 32)  // 16 bits

        clmp := scaledSlice
        val clamped        = Mux(cin_reg >= scaledSlice, scaledSlice, cin_reg)
        val elems_per_word = params.XBitWidth.U/input_bits
        val temp_mul       = clamped * elems_per_word

        x_slice_input_reg := scaledSlice
        XS                := clamped

        x_elems_per_reg := elems_per_word
        w_elems_per_reg := params.WBitWidth.U/weight_bits

        //before load X
        all_available_elems_x := (temp_mul).min(cin_reg)
        mul1                  := 0.U
        mul2                  := temp_mul

        //dynamic bitwitdh parameters
        val p_bits = input_bits + weight_bits
        mask_in                 := (1.U << input_bits) - 1.U
        chunks_per_Product_bits := p_bits
        mask_p                  := (1.U << p_bits) - 1.U
        mask_w                  := (1.U << weight_bits) - 1.U
        block_rows              := (1.U << (weight_bits  -2.U ))

        val blocks_in_Sync_mem_max      = ( 1.U << (params.WBitWidth.U - weight_bits ) ) * params.Mem_row_factor.U  // How many blocks can fit in one psysical memory
        val help =  (clamped + mems_per_ys.U - 1.U) / mems_per_ys.U //ceiled

        blocks_in_Sync_mem := Mux(help < blocks_in_Sync_mem_max, help,blocks_in_Sync_mem_max )

        //////////////////
        // val rounded_blocks = ((blocks_in_Sync_mem + 3.U) >> 2) << 2 //TODO
        // blocks_in_Sync_mem := rounded_blocks
        //////////////////

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
        input_bits   := (if (params.FP16_ACTIVATIONS) 16.U else rs1(5,0))
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
    // === State 2: load activation rows into scratchpads ===
    is(sLoadX) {
      if(params.DEBUG){
        PC_loadX := PC_loadX + 1.U
      }

      // send request to DMA in A Channel
      when(dma_send && !dma.io.busy && !SplitEnable) {

        j_x := dmaRequest(
          j_x,
          x_elems_per_reg,
          elemsPerChunk_in,
          adr_X,
          RegsPerChunk_in,
          all_available_elems_x
        )

      }

      // response fron DMA in D channel
      when(dma.io.d_valid) {

        j_x_temp := dmaResponce(
          j_x_temp,
          x_elems_per_reg,
          elemsPerChunk_in,
          RegsPerChunk_in,
          input_bits,
          params.XBitWidth.U,
          all_available_elems_x,
          true   // update valid_inputs
        )

        offset_in_chunk_1 := chunkInfoModule_D.io.offset_in_chunk_1
      }

      // ------------- Sync Read Memory System ----- VERSION 2 ------------- //

      when(dma.io.d_valid) {
        if (params.y_slice == 1) {
          storePackedResponse(I_MEM, input_bits, dma_counter_d,
            packedReadCount(elements_to_read_temp, dma_counter_d, elemsPerChunk_in),
            mul1 + j_x_temp, dataResponse)
        } else {
          // Each row has b input banks. Repack a row into its own banks while keeping
          // the source address/beat offset specific to that row and K block.
          for (y <- 0 until params.y_slice) {
            when(Rin_cnt === y.U) {
              storePackedResponse(I_MEM.slice(y * weightBanks, (y + 1) * weightBanks),
                input_bits, dma_counter_d,
                packedReadCount(elements_to_read_temp, dma_counter_d, elemsPerChunk_in),
                mul1 + j_x_temp, dataResponse)
            }
          }
        }
      }
      // ------------- Sync Read Memory System ----- VERSION 2 ------------- //

      // ------------- Sync Read Memory System ------------------ //

      //  // store responce data in Activation memory blocks
      //  when(dma.io.d_valid) {

      //     val data  = dma.io.readData

      //     // loop through all memories
      //     for (i <- 0 until totalSyncMems) {
      //       when (counter_mem === i.U) {
      //         I_MEM(i).write(counter_row, data)
      //       }
      //     }

      //     // update counters
      //     when (counter_reg.asSInt >=  (blocks_in_Sync_mem.asSInt - (params.DMA_bits / params.XBitWidth).S)) {
      //       counter_row := 0.U
      //       counter_reg := 0.U
      //       counter_mem := counter_mem + 1.U
      //     } .otherwise {
      //       counter_row := counter_row + 1.U
      //       counter_reg := counter_reg +  (params.DMA_bits/params.XBitWidth).U
      //     }

      //   }

      // // ------------- Sync Read Memory System ------------------ //

      when(!dma_resp && !SplitEnable ) {
        dma_send      := true.B && (Rin_cnt < YS -1.U)
        dma_resp      := true.B && (Rin_cnt < YS -1.U)
        dma_counter_a := 0.U
        dma_counter_d := 0.U
        Rin_cnt       := Rin_cnt + 1.U
        if (params.y_slice > 1) {
          // All active rows have the same K extent; keep one per-row count.
          when(Rin_cnt < YS - 1.U) { valid_inputs := 0.U }
        }
        j_x           := Mux(Rin_cnt < YS -1.U,j_x_start,j_x)
        i_x           := Mux(Rin_cnt < YS -1.U,i_x + 1.U,i_x)
        mul1          := Mux(Rin_cnt < YS -1.U,cin_reg*(i_x + 1.U),mul1)

        //before load X
        val XS_temp   = Mux(cin_reg - j_x_start  >= x_slice_input_reg,x_slice_input_reg,cin_reg - j_x_start )
        val mul1_temp = (i_x + 1.U) * cin_reg

        //compute loigc
        val mul2_temp = XS_temp * x_elems_per_reg

        XS            := XS_temp
        mul2          := mul2_temp
        mul1          := mul1_temp

        //before load X
        val bit_offset_in_byte = ((mul1_temp + j_x_start) * input_bits) & (params.XBitWidth.U - 1.U)
        val skipInFirstByte = bit_offset_in_byte / weight_bits
        all_available_elems_x := (XS * x_elems_per_reg).min(cin_reg - j_x_start)
        j_x_temp              := j_x_start

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
          I_vals(sync_mem_idx) := I_MEM(sync_mem_idx).read(0.U, Rin_cnt + 1.U === YS )
        }
        // ------------- Sync Read Memory System ------------------ //

      }

      when(Rin_cnt === YS && !SplitEnable){
        val x_slice_reg_elems = x_slice_input_reg * x_elems_per_reg
        load_x_cnt := load_x_cnt + 1.U
        j_x_start  := Mux(j_x_start + x_slice_reg_elems   >= cin_reg , 0.U,j_x_start + x_slice_reg_elems )
        i_x_start  := Mux(j_x_start + x_slice_reg_elems   >= cin_reg , i_x_start + YS -0.U,i_x_start)
        cycleCount_1 := 0.U
        Products.foreach(_ := 0.U)

        val remain_in_column      = cin_reg - i_w
        val remain_in_column_regs = (remain_in_column + w_elems_per_reg -1.U)/w_elems_per_reg
        val XS_W_temp             = Mux(remain_in_column_regs >= x_slice_weights_reg,x_slice_weights_reg,remain_in_column_regs)  //max_w_elems
        XS_W  := XS_W_temp
        dirty_elems_w.foreach(_ := 0.U)

        //before load W
        val temp_mul1 = j_w * cin_reg
        val temp_mul2 = if (params.y_slice == 1) XS_W_temp * w_elems_per_reg
        else (XS_W_temp * w_elems_per_reg).min(valid_inputs)
        mul1 := temp_mul1
        mul2 := temp_mul2
        val bit_offset_in_byte = (((temp_mul1)) * weight_bits) & (params.WBitWidth.U - 1.U)
        val skipInFirstByte    = bit_offset_in_byte / weight_bits
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

        state      := sGenerateRegFiles
        // Seed selection once after X is loaded, before producing its LUT rows.
        activationAddress.io.restart := true.B
        Products.foreach(_ := 0.U)

        // ------------- Sync Read Memory System ------------------ //
        counter_row := 0.U //TODO
        counter_mem := 0.U
        offset_counter := 0.U
        // ------------- Sync Read Memory System ------------------ //

      }

    }

    // === State 3: generate lookup products for each activation lane ===
    is(sGenerateRegFiles) {
      if(params.DEBUG){
        PC_Generate := PC_Generate + 1.U
      }

      val start_row = counter_1 << shiftAmt

      for (sync_mem_idx <- 0 until totalSyncMems) {

        when(cycleCount_1 =/= 0.U ) {
          val finalAddressInBRAM =  start_row + (cycleCount_1 -1.U)
          temp_muls_mem(sync_mem_idx).write(finalAddressInBRAM, Products(sync_mem_idx))
        }

      }

      // Product Generator Mesh
      for (sync_mem_idx <- 0 until totalSyncMems) {
        val gen = Product_Generator_Mesh(sync_mem_idx)
        Products(sync_mem_idx) := gen.io.Product_out
      }

      cycleCount_1 := cycleCount_1 + 1.U

      when(cycleCount_1 === block_rows) {
        activationAddress.io.advance := true.B
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
      val enable_read = ((cycleCount_1 === block_rows - 2.U) && (offset_counter === 3.U)) || (block_rows === 1.U)
      val row         = Mux(offset_counter === 3.U, counter_row + 1.U, counter_row)

      // Read request
      val readData = Seq.tabulate(totalSyncMems)(i => I_MEM(i).read(row, enable_read))

      // Delay the enable signal by 1 cycle
      val read_valid = RegNext(enable_read, init=false.B)

      // Capture results (cycle N+1)
      when (read_valid) {
        for (i <- 0 until totalSyncMems) {
          I_vals(i) := readData(i)
        }
      }
      // ------------- Sync Read Memory System ------------------ //

      when(counter_1 === blocks_in_Sync_mem - 1.U && cycleCount_1 === (block_rows )) { // 4bits => 7 cycles delay
        Rin_cnt       := 0.U
        j_x           := j_x_start
        i_x           := i_x_start
        cycleCount_1  := 0.U
        counter_1     := 0.U

        READ_ON       := true.B
        read_on_delay := false.B
        delay         := true.B
        delay_counter := 0.U

        // ------------- Sync Read Memory System ------------------ //
        counter_row := 0.U
        counter_mem := 0.U
        offset_counter := 0.U
        offset_counter_1 := 0.U
        cell_counter   := 0.U
        // Rewind X after generation so column zero reuses its first activation.
        activationAddress.io.restart := true.B
        // ------------- Sync Read Memory System ------------------ //

        state            := sLoadW_and_sSelectAndAccumulate
      }

    }
    // === State 4: consume weight buffers, select products and accumulate ===
    is(sLoadW_and_sSelectAndAccumulate) {

      if(params.DEBUG){
        PC_loadW_and_Select := PC_loadW_and_Select + 1.U
      }

      when(buffers_mode.reduce(_ || _)) { // Select and accumualte Phase

        val buff_index = select_buff_idx

        // Match the old one-cycle weight / two-cycle activation address pipelines.
        // Capture every active selection cycle, including the final pipeline drain.
        weightCellS2 := cell_counter
        weightOffsetS2 := offset_counter_1
        activationCellS3 := RegNext(activationAddress.io.cell, 0.U)
        activationBankShiftS3 := RegNext(activationAddress.io.bankShift, 0.U)

        if(params.DEBUG) {
          PC_Select := PC_Select + 1.U
        }

        // ------------- Sync Read Memory System ------------------ //

        // Request phase (cycle N)
        val enable_read = READ_ON && (offset_counter === 0.U)
        val row         = Mux(offset_counter === 3.U, counter_row + 1.U, counter_row)

        // Read request
        val readData = Seq.tabulate(totalSyncMems)(i => I_MEM(i).read(row, enable_read))

        // Delay the enable signal by 1 cycle
        val read_valid = RegNext(enable_read, init=false.B)

        // Capture results (cycle N+1)
        for (i <- 0 until totalSyncMems) {
          I_vals(i) := readData(i)

        }

        val offset_counter_delay = RegNext(RegNext(offset_counter))

        when (offset_counter === 3.U && READ_ON && cycleCount_2 === x_elems_per_reg - 1.U) {
          offset_counter := 0.U
          counter_row    := counter_row + 1.U
        }.otherwise {
          offset_counter    := Mux(cycleCount_2 === x_elems_per_reg - 1.U ,offset_counter + 1.U,offset_counter)
        }

        // ------------- Sync Read Memory System ------------------ //
        val enable_read_w = READ_ON && (elements_counter === 0.U )

        val W_wire = Wire(Vec(buffers, Vec(weightBanks, UInt(params.DMA_bits.W))))

        for (b <- 0 until buffers) {
          val valid_read = (buff_index === b.U) && enable_read_w
          for (i <- 0 until weightBanks) {
            W_wire(b)(i) :=  W_MEM(b)(i).read(row_weight,valid_read)
          }
        }

        when(elements_counter === elemsPerChunk_w * weightBanks.U - weightBanks.U ){
          elements_counter := 0.U
          row_weight := row_weight + 1.U
        }.otherwise {
          elements_counter := elements_counter + weightBanks.U
        }

        // ------------- Sync Read Memory System ------------------ //

        val start_row = RegNext(counter_2 << shiftAmt)
        val shift_blocks = Log2(blocks_in_Sync_mem)
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

          // mapping to 2D space
          val global_block_idx   =  sync_mem_idx.U * blocks_in_Sync_mem + counter_2
          val x_th_x_reg = global_block_idx  // & (total_blocks_per_y - 1.U) // TODO
          val y_th_x_reg =
          if (params.y_slice > 1)
          (sync_mem_idx / weightBanks).U
          else
          0.U

          val valid_x = if (params.y_slice == 1) valid_inputs > x_th_x_reg * x_elems_per_reg
          else ((sync_mem_idx / weightBanks).U < YS) &&
          (valid_inputs > (((sync_mem_idx % weightBanks).U * blocks_in_Sync_mem + counter_2) * x_elems_per_reg))

          Valid_In_Vec(sync_mem_idx)  := valid_x

          //Mux and shift logic
          val w_idx = Mux(x_elems_per_reg === 1.U, x_th_x_reg, x_th_x_reg << 1) + cycleCount_2
          val linear_w    = w_idx  //+ dirty_elems_w(dirty_elems_w_counter_1)

          // ------------- Sync Read Memory System ------------------ //
          // Data_Vec(sync_mem_idx) := global_block_idx
          // ------------- Sync Read Memory System ------------------ //

          val W_reg_Index = Wire(UInt(linear_w.getWidth.W))
          val w_offset    = Wire(UInt(log2Ceil(4).W))
          val shift_amt   = Wire(UInt((w_offset.getWidth + 3).W))

          when (w_elems_per_reg === 1.U) {
            // W_reg_Index := linear_w
            w_offset    := 0.U
            shift_amt   := 0.U  // 8-bit weights
          } .elsewhen (w_elems_per_reg === 2.U) {
            // W_reg_Index := linear_w >> 1
            w_offset    := linear_w(0)
            shift_amt   := linear_w(0) << 2  // 4-bit weights
          } .otherwise { // w_elems_per_reg === 4.U
            // W_reg_Index := linear_w >> 2
            w_offset    := linear_w(1, 0)
            shift_amt   := linear_w(1, 0) << 1  // 2-bit weights
          }

          val shifted   = shift_amt

          W_reg_Index := linear_w

          //Stage 1  Outputs
          yThXRegVec(sync_mem_idx) := RegNext(y_th_x_reg)
          shiftedVec(sync_mem_idx) := shifted
        }
        cycleCount_2_reg := cycleCount_2

        // ------------------------------------------------------------------------------------------------> stage - 2

        for (sync_mem_idx <- 0 until weightBanks) {

          // Each parallel lane owns this bank; no global-index division.
          val byte_w_idx = weightCellS2
          val buff_idx   = buff_index(buffIdxW - 1, 0)
          val data_64 =  W_wire(buff_idx)(sync_mem_idx)
          val bytes_8 = VecInit(Seq.tabulate(params.DMA_bits / params.WBitWidth)(i => data_64((i + 1) *  params.WBitWidth - 1, i * params.WBitWidth)))
          val W_value = bytes_8(byte_w_idx)
          // Constant shifts replace offset * runtime weight_bits.
          val shift_amt = Mux(weight_bits === 8.U, 0.U,
            Mux(weight_bits === 4.U, weightOffsetS2 << 2, weightOffsetS2 << 1))

          // ------------- Sync Read Memory System ------------------ //

          //Input from previues stages
          val shiftedReg =  W_value >> shift_amt
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

          val w_reg_valid_temp = if (params.FP16_ACTIVATIONS) Valid_In_Vec(sync_mem_idx)
          else (abs_weight_uint =/= 0.U) && Valid_In_Vec(sync_mem_idx)

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
          val  y_th_x_reg =  yThXRegVec(sync_mem_idx)

          //From Stage 2
          val weightLane = sync_mem_idx % weightBanks
          val weight_Sint_part = weight_Sint_part_Reg(weightLane)
          val abs_weight_uint  = abs_weight_uint_Reg(weightLane)
          val row_idx          = row_idx_Reg(weightLane)
          val elemOffset       = elemOffset_Reg(weightLane)
          val w_reg_valid_temp = if (params.y_slice == 1) w_reg_valid_temp_Reg(weightLane)
          else w_reg_valid_temp_Reg(weightLane) && RegNext(Valid_In_Vec(sync_mem_idx))

          val bank_in_idx = rotatedActivationBank(sync_mem_idx, activationBankShiftS3)
          val byte_in_idx = activationCellS3
          val data_64 =  I_vals(bank_in_idx)
          val bytes = VecInit(Seq.tabulate(params.DMA_bits / params.XBitWidth)(i => data_64((i + 1) *  params.XBitWidth - 1, i *  params.XBitWidth)))
          val data  = bytes(byte_in_idx)
          // ------------- Sync Read Memory System ------------------ //

          val extender = chunkExtenders(sync_mem_idx)(chunkPartIdx)
          extender.io.data       := data
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

          val extended_activation = sign_X_reg_part.pad(params.XBitWidth + 1)
          val abs_val_new = if (params.FP16_ACTIVATIONS) FP16Activation.significand(data).zext
          else Mux(extended_activation < 0.S, -extended_activation, extended_activation)
          val signed_val  = Mux(product_sign === 1.U, -abs_val_new, abs_val_new)

          val disable = (abs_weight_uint(0) === 0.U) || (abs_weight_uint === 0.U)  || !w_reg_valid_temp
          sum(sync_mem_idx) := RegNext(Mux(disable, 0.S, signed_val))
          if (params.FP16_ACTIVATIONS) {
            val special = data(14, 10).andR
            val infinity = special && !data(9, 0).orR
            val nan = w_reg_valid_temp && special && (data(9, 0).orR || abs_weight_uint === 0.U)
            val infProduct = w_reg_valid_temp && infinity && abs_weight_uint =/= 0.U
            val meta = fp16Meta.get(sync_mem_idx)
            meta.shift := RegNext(FP16Activation.shift(data))
            meta.nonZeroWeight := RegNext(w_reg_valid_temp && abs_weight_uint =/= 0.U)
            meta.flags := RegNext(Cat(infProduct && product_sign, infProduct && !product_sign, nan))
          }

          // ---------------------------------------------
          // 6. Read Partial Product from Temp Memory
          // ---------------------------------------------

          val enable_read        = if (params.FP16_ACTIVATIONS) READ_ON && w_reg_valid_temp && abs_weight_uint =/= 0.U
          else READ_ON && w_reg_valid_temp
          val finalAddressInBRAM = row_idx

          Products(sync_mem_idx) := temp_muls_mem(sync_mem_idx).read(finalAddressInBRAM, enable_read)
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
          when(cycleCount_2 === x_elems_per_reg - 1.U) {
            activationAddress.io.advance := true.B
            when(offset_counter_1 === lastWeightOffset) {
              offset_counter_1 := 0.U
              cell_counter := Mux(cell_counter === (bankSize - 1).U,
                0.U, cell_counter + 1.U)
            }.otherwise {
              offset_counter_1 := offset_counter_1 + 1.U
            }
          }
          cycleCount_2 := Mux(cycleCount_2 === x_elems_per_reg - 1.U , 0.U ,cycleCount_2 + 1.U)
          counter_2    := Mux(cycleCount_2 === x_elems_per_reg - 1.U ,counter_2 + 1.U,counter_2)
          Done         := Done || (cycleCount_2 === x_elems_per_reg - 1.U  && counter_2 === blocks_in_Sync_mem - 1.U)
        }

        // when(counter_2 === blocks_in_Sync_mem - 1.U ){ //new
        //   counter_2 := 0.U
        // }

        when(!delay && !Done_acc ) { // Accumulate values ( delay for first value only 1 cycle delay )

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

                if (params.FP16_ACTIVATIONS) {
                  val meta = fp16Meta.get(product_idx)
                  val aligned = (signedValue.pad(accumulatorWidth) << meta.shift)(accumulatorWidth - 1, 0).asSInt
                  Mux(meta.nonZeroWeight, aligned, 0.S(accumulatorWidth.W))
              } else signedValue
            }

              // Reduce with known SInt type
              signedValues.map(_.pad(accumulatorWidth)).reduce(_ + _)
          })

          for (y <- 0 until params.y_slice) {
            if (params.y_slice == 1) {
              O_reg(0)(y)(w_idx - 0.U) :=  (O_reg(0)(y)(w_idx - 0.U) +  accum(y))
              if (params.FP16_ACTIVATIONS) {
                val flags = (0 until groupSize).map(i => fp16Meta.get(y * groupSize + i).flags).reduce(_ | _)
                fp16Flags.get(y)(w_idx) := fp16Flags.get(y)(w_idx) | flags
              }
            } else {
              when(y.U < YS) {
                O_reg(0)(y)(w_idx) := O_reg(0)(y)(w_idx) + accum(y)
                if (params.FP16_ACTIVATIONS) {
                  val flags = (0 until groupSize).map(i => fp16Meta.get(y * groupSize + i).flags).reduce(_ | _)
                  fp16Flags.get(y)(w_idx) := fp16Flags.get(y)(w_idx) | flags
                }
              }
            }
          }

          Done_acc := RegNext(RegNext(RegNext(Done)))

        }

      }

      when(Done_acc && w_idx =/= cout_reg) { // select next Phase

        offset_counter_1 := 0.U
        cell_counter   := 0.U
        // Every output column replays X from the same initial bank/cell.
        activationAddress.io.restart := true.B

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

        dirty_elems_w_counter_1 := Mux(dirty_elems_w_counter_1 === 2.U, 0.U, dirty_elems_w_counter_1 + 1.U)

        // buff_elems(select_buff_idx)    := 0.U
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

          // --------------------------------------
          i_w           := i_w_start + step_new
          i_w_start     := i_w_start + step_new
          init_cycle    := true.B
          // --------------------------------------

          XS            := XS_temp
          dma_counter_a := 0.U
          dma_counter_d := 0.U
          mul2          := mul2_temp
          mul1          := mul1_temp

          //before load X
          val bit_offset_in_byte = ((mul1_temp + j_x) * input_bits) & (params.XBitWidth.U - 1.U)
          val skipInFirstByte =bit_offset_in_byte / input_bits
          val elems_per_word = params.XBitWidth.U / input_bits
          all_available_elems_x := (XS * elems_per_word).min(cin_reg - j_x)
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

          if (params.y_slice == 1) {
            val remain_in_line = cin_reg - blocks_in_Sync_mem * totalSyncMems.U
            blocks_in_Sync_mem := ((remain_in_line + totalSyncMems.U - 1.U) / totalSyncMems.U) .min(blocks_in_Sync_mem)
          } else {
            blocks_in_Sync_mem := ((XS_temp + weightBanks.U - 1.U) / weightBanks.U).min(blocks_in_Sync_mem)
          }

          state := sLoadX
          valid_inputs :=0.U

        }
        j_w              := 0.U
        w_idx            := 0.U
        cycleCount_2       := 0.U
        counter_2        := 0.U
        offset_counter_1 := 0.U
        cell_counter := 0.U
        Done             := false.B
        Done_acc         := false.B
        // buff_elems.foreach(_ := 0.U)
        lock             := false.B

      }

    }

    // === State 5: convert/store output rows and advance row groups ===
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

        if (params.FP16_ACTIVATIONS) {
          val words = (0 until elems_per_chunk).map { i =>
            val index = dma_counter_a + i.U
            val convert = Module(new Fixed24ToFP32(accumulatorWidth))
            convert.io.value := O_reg(0)(Rin_cnt)(index)
            convert.io.flags := fp16Flags.get(Rin_cnt)(index)
            Mux(i.U < R_elems_per_chunk, convert.io.bits, 0.U(32.W))
          }
          dma.io.writeData := words.zipWithIndex.foldLeft(0.U(params.DMA_bits.W)) { case (packed, (word, i)) =>
            packed | (word << ((i.U + done_elems_in_block) * params.OutBitWidth.U))
          }
        } else if(params.SCALE) {

          val scaleModule = Module(new Scale_Vector)
          val dataVector = VecInit((0 until elems_per_chunk).map(i => Mux(i.U < R_elems_per_chunk, O_reg(0)(Rin_cnt)(dma_counter_a + i.U), 0.S(params.DMA_bits.W))))
          scaleModule.io.inVector := dataVector
          scaleModule.io.scale    := scale

          dma.io.writeData := scaleModule.io.out

        } else {  //NO Scale

          val data_chunk = (0 until elems_per_chunk).foldLeft(0.U(params.DMA_bits.W)) { (acc, i) =>
            acc | Mux( i.U < R_elems_per_chunk,
              O_reg(0)(Rin_cnt)(dma_counter_a + i.U).asUInt << ((i.U + done_elems_in_block) * params.OutBitWidth.U),
              0.U(params.DMA_bits.W)
            )
          }

          dma.io.writeData :=  data_chunk
        }

        // Option 2: Building the mask per byte lane (define beatBytes as total byte lanes in the beat)
        val beatBytes = 8  // Total number of byte lanes in a 64-bit word
        val bytesPerElem = params.OutBitWidth.U >> 3  // Divide by 8
        val startByte = done_elems_in_block * bytesPerElem
        val endByte   = startByte + (R_elems_per_chunk * bytesPerElem) //R_elems_per_chunk
        dma.io.mask := Cat((0 until beatBytes).map { i => (i.U >= startByte) && (i.U < endByte) }.reverse)

        dma.io.addr   := chunk_address
        dma.io.valid  := true.B
        when(dma.io.a_fire) {
          dma_counter_a := dma_counter_a + R_elems_per_chunk
          dma_send      := dma_counter_a < cout_reg - R_elems_per_chunk

          for (i <- 0 until elems_per_chunk) {
            when(i.U < R_elems_per_chunk) {
              O_reg(0)(Rin_cnt)(dma_counter_a + i.U) := 0.S
              if (params.FP16_ACTIVATIONS) fp16Flags.get(Rin_cnt)(dma_counter_a + i.U) := 0.U
            }
          }

          lock    := true.B
          j_o := j_o + R_elems_per_chunk

        }

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

      // Drain accepted DMA requests and buffered responses before completion
      // or before reinitializing the counters for another activation-row group.
      when(Rin_cnt === YS && dma.io.empty) {
        when(loops_cnt === LOOPS -1.U){
          Rin_cnt             := 0.U
          state               := sDELAY
          done_elems_in_block := 0.U
          loops_cnt := 0.U

        }.otherwise{
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

          val XS_temp = Mux(cin_reg >= x_slice_input_reg,x_slice_input_reg,cin_reg)
          XS := XS_temp

          // new YS clamping
          val remain_in_column =  rin_reg - (i_x + 1.U)
          // YS :=  Mux(remain_in_column >= y_slice_Reg,y_slice_Reg,remain_in_column)
          if (params.y_slice > 1) {
            YS := (rin_reg - i_x).min(y_slice_Reg)
          }

          // recompute chunk parameters for next X load
          val elems_per_word = params.XBitWidth.U / input_bits
          all_available_elems_x := (XS_temp * elems_per_word).min(cin_reg)

          // reset load counters
          load_x_cnt := 0.U

          // go to LoadX
          state := sLoadX
          valid_inputs := 0.U
          val clamped        = Mux(cin_reg >= clmp, clmp, cin_reg)
          if (params.Rin > 1) {
            // A short final K block can shrink the bank span. Restore the
            // full span before loading the first block of the next row group.
            val initialBlocks = (clamped + weightBanks.U - 1.U) / weightBanks.U
            val capacity = (1.U << (params.WBitWidth.U - weight_bits)) * params.Mem_row_factor.U
            blocks_in_Sync_mem := initialBlocks.min(capacity)
          }
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

          // Calculate dirty bits in the last chunk
          val dirty_bits = row_bits % params.DMA_bits.U
          val dirty_elements = dirty_bits / input_bits
          // storePackedResponse repacks each new row from bank offset 0.
          // Keeping the old source-beat offset here shifts later rows.
          dirty_elems_x := (if (params.y_slice == 1 && params.Rin == 1) dirty_elements else 0.U)

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
    // === State 6: wait for DMA drain and completion acknowledgement ===
    is(sDELAY) {
      io.busy     := !dma.io.empty
      cmd.ready   := dma.io.empty
      when(cmd.fire && (mode_6)) {
        state := sIdle
      }

    }

  }

  // === Performance-counter readback (outside the state switch) ===
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

  // === Background weight prefetch (overlaps generation and selection) ===
  when( ( buffers_mode.map(! _).reduce(_ || _)  && w_count =/= cout_reg ) && (state === sLoadW_and_sSelectAndAccumulate || state === sGenerateRegFiles)   ) { // Load W Phase

    val buff_index = load_w_buff_idx
    if(params.DEBUG){
      PC_loadW := PC_loadW + 1.U
    }

    when(init_cycle){
      step_new := all_available_elems
      init_cycle := false.B
    }

    // send request to DMA in A Channel
    when(dma_send && !dma.io.busy && !SplitEnable) {

      i_w := dmaRequest(
        i_w,
        w_elems_per_reg,
        elemsPerChunk_w,
        adr_W,
        RegsPerChunk_w,
        all_available_elems
      )

    }

    // response from DMA in D channel
    when(dma.io.d_valid) {

      i_w_temp := dmaResponce(
        i_w_temp,
        w_elems_per_reg,
        elemsPerChunk_w,
        RegsPerChunk_w,
        weight_bits,
        params.WBitWidth.U,
        all_available_elems,
        false
      )

      offset_in_chunk_2 := chunkInfoModule_D.io.offset_in_chunk_1
    }

    when(dma.io.d_valid) {
      for (b <- 0 until buffers) {
        when(buff_index === b.U) {
          storePackedResponse(W_MEM(b), weight_bits, dma_counter_d,
            packedReadCount(elements_to_read_temp, dma_counter_d, elemsPerChunk_w),
            mul1 + i_w_temp, dataResponse_w)
        }
      }
      valid_weights(buff_index) := valid_weights(buff_index) +
      packedReadCount(elements_to_read_temp, dma_counter_d, elemsPerChunk_w)
    }

    // when(dma.io.d_valid) { // // store responce data in Weights memory blocks

    //   // ------------- Sync Read Memory System ------------------ //
    //  valid_weights(buff_index) := valid_weights(buff_index) +  bytes_to_read //elements_to_read_temp

    // val data = dma.io.readData

    //   for (b <- 0 until buffers) {
    //     for (i <- 0 until totalSyncMems) {
    //       when (buff_index === b.U && (counter_mem_w === i.U)) {
    //         W_MEM(b)(i).write(counter_row_w, data)
    //       }
    //     }
    //   }

    // // update counters
    // when (counter_reg_w.asSInt >= (blocks_in_Sync_mem.asSInt - (elemsPerChunk_w).asSInt)) {
    //   counter_row_w := 0.U
    //   counter_reg_w := 0.U
    //   counter_mem_w := counter_mem_w + 1.U
    // } .otherwise {
    //   counter_row_w := counter_row_w + 1.U
    //   counter_reg_w := counter_reg_w +  (elemsPerChunk_w)
    // }

    // }

    when(!dma_resp && !SplitEnable) {

      // ------------- Sync Read Memory System ------------------ //
      counter_row_w := 0.U
      counter_mem_w := 0.U
      // counter_reg_w := 0.U
      // ------------- Sync Read Memory System ------------------ //

      i_w                      := i_w_start
      j_w                      := j_w + 1.U
      f_idx                    := 0.U
      dma_counter_a            := 0.U
      dma_send                 := true.B
      dma_resp                 := true.B
      lock                     := false.B
      // buff_elems(buff_index)   := dma_counter_d
      dma_counter_d            := 0.U
      buffers_mode(buff_index) := true.B

      ///next W elements
      val remain_in_column_regs = (cin_reg - i_w_start  + w_elems_per_reg -1.U)/w_elems_per_reg
      val XS_W_temp             = Mux(remain_in_column_regs >= x_slice_weights_reg,x_slice_weights_reg,remain_in_column_regs)
      XS_W := XS_W_temp
      ///next W elements

      load_w_buff_idx := load_w_buff_idx + 1.U
      w_count         := w_count + 1.U

      //before load W
      val temp_mul1 = (j_w + 1.U) * cin_reg
      val temp_mul2 = if (params.y_slice == 1) XS_W_temp * w_elems_per_reg
      else (XS_W_temp * w_elems_per_reg).min(valid_inputs)

      mul1 := temp_mul1
      mul2 := temp_mul2

      val bit_offset_in_byte = (((temp_mul1) + i_w_start) * weight_bits) & (params.WBitWidth.U - 1.U)
      val skipInFirstByte    = bit_offset_in_byte / weight_bits
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
      elements_to_read_temp := Mux(chunkInfoModule_D.io.elements_to_read_temp <= all_available_elems_next , chunkInfoModule_D.io.elements_to_read_temp, all_available_elems_next)

    }
  }

}

class WithLUMAXAccelerator extends Config((site, here, up) => {
    case BuildRoCC => Seq(
    (p: Parameters) => {
      implicit val implicitParams: Parameters = p
      implicit val valName: ValName = ValName("MatMul_LUMAX_example")

      // Pass fbus to LUMAXExample
      LazyModule(
      new LUMAXExample(
      opcodes = OpcodeSet.all,            // Opcode used for the accelerator
      params  = LUMAX_Config.LUMAX_Config  // Use the config from the LinearFilterConfig object
    ) {

    }
    )
  }
  )
})
