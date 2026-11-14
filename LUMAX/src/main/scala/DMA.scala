package LUMAX_PACKAGE

import chisel3._
import chisel3.util._
import freechips.rocketchip.subsystem.CacheBlockBytes
import org.chipsalliance.cde.config.Parameters
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
  require(ID >= 1, "DMA needs at least one request slot")

  val (mem, edge) = outer.node.out(0)
  // A source remains occupied until its response is delivered to LUMAX, not
  // merely until TileLink returns it. This also reserves early-response storage.
  val outstanding = RegInit(VecInit(Seq.fill(ID)(false.B)))
  val sourceWidth = log2Ceil(ID).max(1) // Keep a legal one-bit source for ID=1.
  val requestOrder = Module(new Queue(UInt(sourceWidth.W), ID))
  val responseData = Reg(Vec(ID, UInt(params.DMA_bits.W)))
  val responseCorrupt = Reg(Vec(ID, Bool()))
  val responseDenied = Reg(Vec(ID, Bool()))
  val responseValid = RegInit(VecInit(Seq.fill(ID)(false.B)))
  val available = VecInit(outstanding.map(!_)).asUInt
  val nextSource = Wire(UInt(sourceWidth.W))
  nextSource := PriorityEncoder(available)
  val hasFree = available.orR

  val a_bits = Wire(mem.a.bits.cloneType)
  // A complete transfer is a PutFullData, not a masked read/modify/write.
  // Compare against the transfer mask (also correct for sub-beat sizes).
  // Partial output tails keep their original byte enables and PutPartialData.
  val fullWrite = edge.Put(fromSource = nextSource, toAddress = io.addr,
    lgSize = io.log_S, data = io.writeData)
  val writeMask = io.mask(params.DMA_Bytes - 1, 0)
  val useFullWrite = fullWrite._1 && writeMask === edge.mask(io.addr, io.log_S)
  a_bits := Mux(io.mode === 0.U,
    edge.Get(fromSource = nextSource, toAddress = io.addr, lgSize = io.log_S)._2,
    Mux(useFullWrite, fullWrite._2,
      edge.Put(fromSource = nextSource, toAddress = io.addr, lgSize = io.log_S, data = io.writeData, mask = writeMask)._2)
  )

  mem.a.bits := a_bits
  mem.a.valid := io.valid && hasFree && requestOrder.io.enq.ready
  io.a_fire := mem.a.fire
  io.a_cor := mem.a.bits.corrupt

  requestOrder.io.enq.valid := mem.a.fire
  requestOrder.io.enq.bits := nextSource

  /*
   Previous registered response path, before the bypass optimization.
   Reference only: this block is inactive and is not elaborated.
   To restore it, replace the active response block below; do not enable both.
   Request issue and source-ID retirement remain shared and unchanged.

  // LUMAX consumes every d_valid pulse (the legacy d_ready input is unused).
  // Capture both Get data and Put acknowledgements, then retire in issue order.
  mem.d.ready := true.B
  when(mem.d.fire) {
    val source = mem.d.bits.source
    assert(source < ID.U, "DMA received an out-of-range response source")
    assert(outstanding(source) || (mem.a.fire && nextSource === source),
      "DMA received a response without an accepted request")
    assert(!responseValid(source), "DMA response slot overwritten")
    responseData(source) := mem.d.bits.data
    responseCorrupt(source) := mem.d.bits.corrupt
    responseDenied(source) := mem.d.bits.denied
    responseValid(source) := true.B
  }

  val oldest = requestOrder.io.deq.bits
  val deliver = requestOrder.io.deq.valid && responseValid(oldest)
  io.readData := responseData(oldest)
  io.d_valid := deliver
  io.d_cor := responseCorrupt(oldest)
  io.d_den := responseDenied(oldest)
  io.tag := oldest
  requestOrder.io.deq.ready := deliver
  */

  // LUMAX consumes every d_valid pulse (the legacy d_ready input is unused).
  // Retire at most one Get/Put response per cycle, strictly in request order.
  mem.d.ready := true.B
  val oldest = requestOrder.io.deq.bits
  val buffered = requestOrder.io.deq.valid && responseValid(oldest)
  // A queued head response can flow directly to LUMAX. Replies for later
  // requests still reserve their source slots and wait in the existing buffer.
  // An empty request queue cannot bypass, including a same-cycle new request.
  val bypass = requestOrder.io.deq.valid && !responseValid(oldest) &&
    mem.d.fire && (mem.d.bits.source === oldest)

  when(mem.d.fire) {
    val source = mem.d.bits.source
    assert(source < ID.U, "DMA received an out-of-range response source")
    assert(outstanding(source) || (mem.a.fire && nextSource === source),
      "DMA received a response without an accepted request")
    assert(!responseValid(source), "DMA response slot overwritten")
    // Forwarded responses are not also buffered: the normal queue dequeue
    // below retires them and frees their source exactly once on this edge.
    when(!bypass) {
      responseData(source) := mem.d.bits.data
      responseCorrupt(source) := mem.d.bits.corrupt
      responseDenied(source) := mem.d.bits.denied
      responseValid(source) := true.B
    }
  }

  val deliver = buffered || bypass
  io.readData := Mux(bypass, mem.d.bits.data, responseData(oldest))
  io.d_valid := deliver
  io.d_cor := Mux(bypass, mem.d.bits.corrupt, responseCorrupt(oldest))
  io.d_den := Mux(bypass, mem.d.bits.denied, responseDenied(oldest))
  io.tag := oldest
  requestOrder.io.deq.ready := deliver

  when(mem.a.fire) {
    outstanding(nextSource) := true.B
  }
  when(requestOrder.io.deq.fire) {
    responseValid(oldest) := false.B
    outstanding(oldest) := false.B
  }

  assert(PopCount(outstanding) === requestOrder.io.count,
    "DMA request queue and occupied slots disagree")
  for (source <- 0 until ID) {
    assert(!responseValid(source) || outstanding(source),
      "DMA buffered response lost its request slot")
  }

  io.SourceOut := nextSource
  io.busy := !hasFree || !requestOrder.io.enq.ready
  io.empty := !outstanding.asUInt.orR
  io.err := false.B // Corrupt/denied response flags are exposed separately.
}
