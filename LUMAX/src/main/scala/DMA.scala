package LUMAX_PACKAGE

import chisel3._
import chisel3.util._
import freechips.rocketchip.subsystem.CacheBlockBytes
import org.chipsalliance.cde.config.Parameters
// import freechips.rocketchip.diplomacy.{LazyModule, LazyModuleImp, IdRange}
import freechips.rocketchip.diplomacy._
import freechips.rocketchip.tilelink._
import LUMAX_PACKAGE.LUMAX_Config


import freechips.rocketchip.diplomacy._

class DmaModule(val params: LUMAXParams)(implicit p: Parameters) extends LazyModule {
  val dmaIds = params.Dma_Ids

  val node = TLClientNode(Seq(TLMasterPortParameters.v1(Seq(
    TLClientParameters(
      name = "DMA",
      sourceId = IdRange(0, dmaIds),
      requestFifo = false,
      visibility = Seq(AddressSet(0x0L, 0xFFFFFFFFFFFFFFFFL)) // 64-bit address space
    )
  ))))

  lazy val module = new DmaModuleImp(this)
}
class DmaModuleImp(outer: DmaModule) extends LazyModuleImp(outer) {
  val io = IO(new Bundle {
    val addr       = Input(UInt(64.W))
    val log_S      = Input(UInt(64.W))
    val mode       = Input(UInt(2.W))
    val valid      = Input(Bool())
    val mask       = Input(UInt(64.W))
    val writeData = Input(UInt((outer.params.DMA_bits).W))
    val a_fire     = Output(Bool())
    val a_cor      = Output(Bool())
    val readData   = Output(UInt((outer.params.DMA_bits).W)) 
    val d_ready    = Input(Bool())
    val d_valid    = Output(Bool())
    val d_cor      = Output(Bool())
    val d_den      = Output(Bool())
    val tag        = Output(UInt(64.W))
    val busy       = Output(Bool())
    val empty      = Output(Bool())
    val err        = Output(Bool())
    val SourceOut  = Output(UInt(64.W))
  })

  val params = outer.params
  val ID = params.Dma_Ids

  val (mem, edge) = outer.node.out(0)
  val outstanding = RegInit(VecInit(Seq.fill(ID)(false.B)))
  val available = VecInit(outstanding.map(!_)).asUInt
  val nextSource = PriorityEncoder(available)
  val hasFree = available.orR

  val a_bits = Wire(mem.a.bits.cloneType)
  a_bits := Mux(io.mode === 0.U,
    edge.Get(fromSource = nextSource, toAddress = io.addr, lgSize = io.log_S)._2,
    edge.Put(fromSource = nextSource, toAddress = io.addr, lgSize = io.log_S, data = io.writeData, mask = io.mask)._2
  )

  mem.a.bits := a_bits
  mem.a.valid := io.valid // && hasFree
  io.a_fire := mem.a.fire //()
  io.a_cor := mem.a.bits.corrupt

  mem.d.ready := true.B
  io.readData := mem.d.bits.data
  io.d_valid := mem.d.valid //    mem.d.fire()
  io.d_cor := mem.d.bits.corrupt
  io.d_den := mem.d.bits.denied
  io.tag := mem.d.bits.source

  when(mem.a.fire) {
    outstanding(nextSource) := true.B
  }
 
  when(mem.d.fire) {
    outstanding(mem.d.bits.source) := false.B
  }

  io.SourceOut := nextSource
  io.busy := outstanding.reduce(_ && _)
  io.empty := outstanding.reduce(_ && !_)
  io.err := false.B // Optional error logic can go here
}
