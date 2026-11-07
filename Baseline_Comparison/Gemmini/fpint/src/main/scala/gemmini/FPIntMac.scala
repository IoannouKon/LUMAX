package gemmini

import chisel3._
import chisel3.util._
import hardfloat._

/** FP16 activation x signed W2/W4/W8, followed by a rounded FP32 addition.
  * The only product multiplier is 11 x weightBits. A finite FP16 x INT8
  * product is exactly representable in FP32, so product encoding introduces
  * no intermediate rounding. FP32 addition supplies the one rounding step.
  * No integer-to-FP converter or general FP multiplier is instantiated.
  */
class FPIntMac(weightBits: Int) extends Module {
  require(Set(2, 4, 8).contains(weightBits))
  val io = IO(new Bundle {
    val activation = Input(UInt(16.W))
    val weight = Input(SInt(weightBits.W))
    val partial = Input(UInt(32.W))
    val result = Output(UInt(32.W))
  })

  val x = io.activation
  val exponent = x(14, 10)
  val fraction = x(9, 0)
  val mantissa = Cat(exponent.orR, fraction)
  val negativeWeight = io.weight < 0.S
  // Keep the magnitude at W bits: abs(-128), abs(-8), abs(-2) must fit.
  val magnitude = Wire(UInt(weightBits.W))
  magnitude := Mux(negativeWeight, (~io.weight.asUInt + 1.U), io.weight.asUInt)
  val product = mantissa * magnitude
  val leading = (product.getWidth - 1).U - PriorityEncoder(Reverse(product))
  val normalized = (product.pad(24) << (23.U - leading))(23, 0)
  val outputExponent = Mux(exponent === 0.U, 1.U, exponent).zext - 25.S + leading.zext + 127.S
  val sign = x(15) ^ negativeWeight
  val isNaN = exponent.andR && fraction.orR
  val invalid = exponent.andR && magnitude === 0.U
  val finite = Cat(sign, outputExponent.asUInt(7, 0), normalized(22, 0))
  val encoded = Mux(isNaN || invalid, "h7fc00000".U,
    Mux(exponent.andR, Cat(sign, "h7f800000".U(31.W)),
      Mux(product === 0.U, Cat(sign, 0.U(31.W)), finite)))

  val add = Module(new AddRecFN(8, 24))
  add.io.subOp := false.B
  add.io.a := recFNFromFN(8, 24, encoded)
  add.io.b := recFNFromFN(8, 24, io.partial)
  add.io.roundingMode := consts.round_near_even
  add.io.detectTininess := consts.tininess_afterRounding
  io.result := fNFromRecFN(8, 24, add.io.out)
}
