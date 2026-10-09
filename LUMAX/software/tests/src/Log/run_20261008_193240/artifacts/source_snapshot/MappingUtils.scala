package LUMAX_PACKAGE

import chisel3._
import chisel3.util._

object MappingUtils {

  def mapIndex(
    idx: UInt,
    weight_bits: UInt,
    blocks_in_Sync_mem: UInt,
    bankSize: UInt,
    totalBanks: UInt,
    WBitWidth: Int
  ): (UInt, UInt, UInt, UInt) = {
   

    //--- New version 
    // 1. BANK
    val bank = (idx / blocks_in_Sync_mem) % totalBanks

    val local = idx % blocks_in_Sync_mem
    
    // 3. PACKING
    val elementsPerCell = WBitWidth.U / weight_bits

    // 2. ROW
    val row = local / (bankSize * elementsPerCell)
    val inRow = local % (bankSize * elementsPerCell)

    val wrapped_index = inRow % bankSize
    val cell = inRow / elementsPerCell
    val offset = wrapped_index % elementsPerCell
   
    // val cell   = inRow / elementsPerCell
    // val offset = inRow % elementsPerCell
    
    // //--- Old version 
    // val S = bankSize
    // val B = totalBanks
    // val rows_used_per_bank =  (blocks_in_Sync_mem + S - 1.U) / S // ceil(blocks_in_Sync_mem / S)
    
    // // x_th_x_reg is your linear element index
    // val chunkIdx    = idx / S
    // val bank     = (chunkIdx / rows_used_per_bank) % B
    // val row = chunkIdx % rows_used_per_bank
    // val cell  = idx % S
    // val offset = 0.U

 
    (bank, row, cell, offset)
  }
}