
package Data_Reuse

import chisel3._
import chisel3.util._

object ChunkUtils {
  //function for A request adn D respocne indexing   
  def calcChunkInfo(
    i_x: UInt,
    cin_reg: UInt,
    j_x: UInt,
    j_x_temp: UInt,
    XS: UInt,
    x_elems_per_reg: UInt,
    elemsPerChunk_in: UInt,
    adr_X: UInt,
    RegsPerChunk_in: UInt,
    dma_counter_d: UInt
  ): (
    UInt, UInt, UInt, UInt, UInt, UInt
  ) = {
    val mul1 = i_x * cin_reg
    val linear_index = mul1 + j_x
    val linear_index_temp = mul1 + j_x_temp

    val mul2 = XS * x_elems_per_reg
    val ements_per_XS = mul2.min(cin_reg - j_x)
    val ements_per_XS_temp = mul2.min(cin_reg - j_x_temp)

    val elemsPerChunk_cond = elemsPerChunk_in === 8.U

    val chunkIndex = Mux(elemsPerChunk_cond, linear_index >> 3, linear_index >> 4)  // div by elemsPerChunk (8 or 16)
    val offset_in_chunk = Mux(elemsPerChunk_cond, linear_index & 7.U, linear_index & 15.U) // mod elemsPerChunk
    val offset_in_chunk_temp = Mux(elemsPerChunk_cond, linear_index_temp & 7.U, linear_index_temp & 15.U)

    val chunk_address = adr_X + (chunkIndex << 3) // chunkIndex * bytesPerChunk (8)

    val available_elems = elemsPerChunk_in - offset_in_chunk
    val available_elems_temp = elemsPerChunk_in - offset_in_chunk_temp

    val elements_to_read = Mux(ements_per_XS <= available_elems, ements_per_XS, available_elems)
    val elements_to_read_temp = Mux(ements_per_XS_temp <= available_elems_temp, ements_per_XS_temp, available_elems_temp)

    val numerator = elements_to_read_temp + x_elems_per_reg - 1.U
    val x_elems_per_reg_cond = x_elems_per_reg === 1.U
    val bytes_to_read = Mux(x_elems_per_reg_cond, numerator, numerator >> 1)  // div by x_elems_per_reg

    val register_index = Mux(x_elems_per_reg_cond, linear_index_temp, linear_index_temp >> 1) // div by x_elems_per_reg

    val offset_in_chunk_1 = register_index & (RegsPerChunk_in - 1.U) // modulo RegsPerChunk_in

    val reg_idx_start = Mux(x_elems_per_reg === 1.U, dma_counter_d, (dma_counter_d >> 1) + (dma_counter_d & 1.U))

    (
      elements_to_read,
      chunk_address,
      bytes_to_read,
      reg_idx_start,
      offset_in_chunk_1,
      elements_to_read_temp
    )
  }

 

 // function for indexing in A requests 
  def calcChunkInfo_A(
    cin_reg: UInt,
    j_x: UInt,
    x_elems_per_reg: UInt,
    elemsPerChunk_in: UInt,
    adr_X: UInt,
    RegsPerChunk_in: UInt,
    mul1 :UInt, 
    mul2 :UInt
  ): (
    UInt, UInt
  ) = {

    //general arbitary 
    val linear_index  = mul1 + j_x
    val ements_per_XS = mul2.min(cin_reg - j_x)

    // Calculate 
    // val chunkIndex      = linear_index / elemsPerChunk_in
    // val offset_in_chunk = linear_index % elemsPerChunk_in

    // //Mux and Shidt 
    val shiftAmt = Mux(elemsPerChunk_in === 32.U, 5.U,
               Mux(elemsPerChunk_in === 16.U, 4.U,
               Mux(elemsPerChunk_in ===  8.U, 3.U, 2.U)))
    val maskVal  = (1.U << shiftAmt) - 1.U
    val chunkIndex      = linear_index >> shiftAmt
    val offset_in_chunk = linear_index & maskVal


    // val bytesPerChunk = 8
    // val chunk_address    = adr_X + (chunkIndex * bytesPerChunk.U)

    val bytesPerChunkShift = 3.U  // because 8 = 2^3
    val chunk_address = adr_X + (chunkIndex << bytesPerChunkShift)

    val available_elems  = elemsPerChunk_in - offset_in_chunk
    val elements_to_read = Mux(ements_per_XS <= available_elems, ements_per_XS, available_elems)

    (
      elements_to_read,
      chunk_address,
    )
  }

//function for  D respocne indexing   
  def calcChunkInfo_D(
    cin_reg: UInt,
    j_x_temp: UInt,
    x_elems_per_reg: UInt,
    elemsPerChunk_in: UInt,
    RegsPerChunk_in: UInt,
    dma_counter_d: UInt,
    mul1: UInt,
    mul2: UInt ,
    input_bits: UInt,
    XBitWidth :UInt,
  ): (
    UInt, UInt, UInt, UInt
  ) = {

    //general arbitary 
    val linear_index_temp = mul1 + j_x_temp
    val ements_per_XS_temp = mul2.min(cin_reg - j_x_temp)


    // Compute 
    // val offset_in_chunk_temp = linear_index_temp % elemsPerChunk_in 

    val offset_in_chunk_temp = linear_index_temp & (elemsPerChunk_in - 1.U)

    val available_elems_temp = elemsPerChunk_in - offset_in_chunk_temp
    val elements_to_read_temp = Mux(ements_per_XS_temp <= available_elems_temp, ements_per_XS_temp, available_elems_temp)
    
    // Compute 
    // val total_bits = elements_tements_per_XS_tempo_read_temp * input_bits
    // val bytes_to_read = total_bits/XBitWidth

    //MUX and SHIFT
    val total_bits = Mux(input_bits === 2.U, elements_to_read_temp << 1,
                Mux(input_bits === 4.U, elements_to_read_temp << 2,
                  Mux(input_bits === 8.U, elements_to_read_temp << 3,
                    elements_to_read_temp << 4  // input_bits == 16.U
                  )
                )
              )

    val shift_amt = Mux(XBitWidth === 16.U, 4.U, 3.U) // 16 → shift by 4, 8 → shift by 3
    val bytes_to_read = total_bits >> shift_amt

   //compute
    // val register_index = linear_index_temp / x_elems_per_reg //new 

    val register_index = Mux(x_elems_per_reg === 1.U, linear_index_temp,
                      Mux(x_elems_per_reg === 2.U, linear_index_temp >> 1,
                        linear_index_temp >> 2 // x_elems_per_reg == 4.U
                      ))

    val offset_in_chunk_1 = register_index & (RegsPerChunk_in - 1.U) // 
    // val offset_in_chunk_1 = register_index % RegsPerChunk_in //new 

    // COmpute
    // // val reg_idx_start = Mux(x_elems_per_reg === 1.U, dma_counter_d, (dma_counter_d >> 1) + (dma_counter_d & 1.U))
    // val reg_idx_start = (dma_counter_d + x_elems_per_reg - 1.U) / x_elems_per_reg //new

    val reg_idx_start = Mux(x_elems_per_reg === 1.U, dma_counter_d,
                   Mux(x_elems_per_reg === 2.U, (dma_counter_d + 1.U) >> 1,
                     (dma_counter_d + 3.U) >> 2  // x_elems_per_reg == 4.U
                   ))



    (
      bytes_to_read,
      reg_idx_start,
      offset_in_chunk_1,
      elements_to_read_temp
    )
  }
}

import Data_Reuse.ChunkUtils._   // import your function from the object
import Data_Reuse.Data_Reuse_Config

class ChunkInfoModule_A(params: DataReuseParams, max_w_elems: Int) extends Module {
  val io = IO(new Bundle {
    val cin_reg           = Input(UInt((log2Ceil(params.Cin + 1).W)))
    val j_x               = Input(UInt((log2Ceil(params.Cin + 1).W)))
    val x_elems_per_reg   = Input(UInt(2.W))
    val elemsPerChunk_in  = Input(UInt(6.W))
    val adr_X             = Input(UInt(64.W))
    val RegsPerChunk_in   = Input(UInt(math.max(1, log2Ceil(64 / params.XBitWidth) + 1).W))
    val mul1              = Input(UInt(16.W))
    val mul2              = Input(UInt(16.W))

    val elements_to_read  = Output(UInt((log2Ceil(max_w_elems) + 1).W))
    val chunk_address     = Output(UInt(64.W))
  })

  // Call your externally defined function
  val (elementsToRead, chunkAddress) = calcChunkInfo_A(
    io.cin_reg,
    io.j_x,
    io.x_elems_per_reg,
    io.elemsPerChunk_in,
    io.adr_X,
    io.RegsPerChunk_in,
    io.mul1,
    io.mul2
  )

  // Wire outputs
  io.elements_to_read := elementsToRead
  io.chunk_address := chunkAddress
}

class ChunkInfoModule_D(params: DataReuseParams, max_w_elems: Int, max_x_regs :Int) extends Module {
  val io = IO(new Bundle {
    val cin_reg           = Input(UInt((log2Ceil(params.Cin + 1).W)))
    val j_x_temp          = Input(UInt((log2Ceil(params.Cin + 1).W)))
    val x_elems_per_reg   = Input(UInt(2.W))
    val elemsPerChunk_in  = Input(UInt(6.W))
    val RegsPerChunk_in   = Input(UInt(math.max(1, log2Ceil(64 / params.XBitWidth) + 1).W))
    val dma_counter_d     = Input(UInt(log2Ceil(math.max(max_x_regs, params.Cout) + 1).W))
    val mul1              = Input(UInt(16.W))
    val mul2              = Input(UInt(16.W))
    val input_bits        = Input(UInt(4.W))
    val max_bits          = Input(UInt(4.W))

    val bytes_to_read         = Output(UInt(4.W)) // Max is ceil(log2(16+2)) = 5~6
    val reg_idx_start         = Output(UInt(((log2Ceil(max_w_elems) + 1).W)))
    val offset_in_chunk_1     = Output(UInt(3.W)) // Assuming max 64 regs/chunk
    val elements_to_read_temp = Output(UInt((log2Ceil(max_w_elems) + 1).W))
  })

  // Call your external function
  val (bytesToRead, regIdxStart, offsetInChunk1, elementsToReadTemp) = calcChunkInfo_D(
    io.cin_reg,
    io.j_x_temp,
    io.x_elems_per_reg,
    io.elemsPerChunk_in,
    io.RegsPerChunk_in,
    io.dma_counter_d,
    io.mul1,
    io.mul2,
    io.input_bits,
    io.max_bits, 
  )

  // Wire outputs
  io.bytes_to_read         := bytesToRead
  io.reg_idx_start         := regIdxStart
  io.offset_in_chunk_1     := offsetInChunk1
  io.elements_to_read_temp := elementsToReadTemp
}