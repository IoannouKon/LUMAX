// Legacy code: unused by the current design. Activation selection now uses
// ActivationAddressCounter.scala; weight selection uses counters in LUMAX_Top.scala.
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
   
    (bank, row, cell, offset)
  }
}
