package LUMAX_PACKAGE

import chisel3._
import chisel3.util._
import LUMAX_PACKAGE.MappingUtils._

class Product_Generator(
    val totalSyncMems: Int,
    val maxParts: Int,
    val WBitWidth: Int,
    val XBitWidth: Int,
    val minWeightBits:Int,
    val RowsPerBlock:Int,
    val DMA_bits:Int,
    val product_bitwidth:Int, 
    val minInputBits:Int
) extends Module {

  val io = IO(new Bundle {

    val sync_mem_idx            = Input(UInt(log2Ceil(totalSyncMems).W))
    val blocks_in_Sync_mem      = Input(UInt(log2Ceil(RowsPerBlock + 1).W))
    val counter                 = Input(UInt(log2Ceil(RowsPerBlock + 2).W))
    val dirty_elems_x           = Input(UInt(log2Ceil(DMA_bits / minInputBits).W))
    val X_vals                  = Input(Vec(totalSyncMems, UInt(DMA_bits.W)))
    val inputs_per_reg          = Input(UInt(2.W))
    val input_bits              = Input(UInt(6.W))
    val valid_inputs            = Input(UInt((totalSyncMems * RowsPerBlock).W))
    val mask_p                  = Input(UInt(log2Ceil((1 << (WBitWidth + XBitWidth))).W))
    val shiftAmt                = Input(UInt(7.W))
    val chunks_per_Product_bits = Input(UInt(log2Ceil(WBitWidth + XBitWidth + 1).W))
    val Product_in              = Input(UInt(product_bitwidth.W))

    val Product_out             = Output(UInt(product_bitwidth.W))
  })


  //--------------------------------------------
  // Instantiate chunkExtenders
  //--------------------------------------------
  val chunkExtenders: Seq[ChunkSignExtender] = Seq.fill(maxParts) {
    Module(new ChunkSignExtender(XBitWidth, minInputBits))
  }

  // Initialize all chunkExtender IOs to safe defaults
  for (j <- 0 until maxParts) {
    val ext = chunkExtenders(j)
    ext.io.data       := 0.U
    ext.io.input_bits := 0.U
    ext.io.idx        := 0.U
  }


  val global_block_idx   =  io.sync_mem_idx * io.blocks_in_Sync_mem + io.counter
  val x_th_x_reg = global_block_idx + io.dirty_elems_x //& (total_blocks_per_y - 1.U) // TODO 
  // val y_th_x_reg =
  //       if (params.y_slice > 1)
  //         global_block_idx >> shift_y
  //       else
   //         0.U
  
        val (bank_in, row_in_bank, byte_index_raw, offset_new) =
            mapIndex(
            idx = x_th_x_reg,
            weight_bits = io.input_bits,
            blocks_in_Sync_mem = io.blocks_in_Sync_mem,
            bankSize = (DMA_bits/XBitWidth).U ,
            totalBanks = totalSyncMems.U,
            WBitWidth = XBitWidth
        )

  //--------------------------------- new code 

        val data_64 =  io.X_vals(bank_in(log2Ceil(totalSyncMems)-1, 0))
        val bytes = VecInit(Seq.tabulate(DMA_bits / XBitWidth)(i => data_64((i + 1) *  XBitWidth - 1, i *  XBitWidth)))
        val elem_start  = x_th_x_reg * io.inputs_per_reg

        // Create a properly-sized wire for the index
        val byte_index = Wire(UInt(log2Ceil(math.max(DMA_bits / XBitWidth, 2)).W))
        byte_index := byte_index_raw
        val data = bytes(byte_index)
        val P_data    =   io.Product_in
        val P_slices  =   Wire(Vec(maxParts, UInt((product_bitwidth).W)))

        //split 
        for (i <- 0 until maxParts) {

          val extender = chunkExtenders(i) //chunkExtenders(sync_mem_idx)(i)
          extender.io.data       := data
          extender.io.input_bits := io.input_bits
          extender.io.idx        := i.U
          val signedChunk = extender.io.out

          val absVal_new = Mux(signedChunk < 0.S, (-signedChunk), signedChunk)
          val valid_x = io.valid_inputs + io.dirty_elems_x > elem_start + i.U 
          val absVal = Mux(valid_x ,absVal_new.asUInt,0.U) 

          val chunk_p   = (P_data >> (i.U * io.chunks_per_Product_bits)) & io.mask_p  // UInt(input_bits.W)
          val scaled    = (absVal << 1) 
          P_slices(i)   := Mux(i.U < (XBitWidth/minInputBits).U , chunk_p + scaled,chunk_p)

        }

        val packed = Wire(UInt(product_bitwidth.W))
        var acc: UInt = 0.U

        //Concat and Accumulate 
        for (i <- 0 until maxParts) {
          val shiftAmt = i.U * io.chunks_per_Product_bits
          val shifted  = P_slices(i) << shiftAmt
          acc = acc | shifted
        }

        packed := acc  
        io.Product_out   := packed
}





