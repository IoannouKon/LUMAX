package gemmini.testsafe
import gemmini._
import gemmini.Arithmetic.FloatArithmetic._
import org.chipsalliance.cde.config.{Config, Parameters}
import freechips.rocketchip.diplomacy.{LazyModule, ValName}
import freechips.rocketchip.tile.BuildRoCC

class WithTestSafeGemmini(w: Int) extends Config((site, here, up) => {
  case BuildRoCC => Seq((p: Parameters) => {
    implicit val q = p
    implicit val v = implicitly[ValName]
    val cfg: GemminiArrayConfig[gemmini.Float, gemmini.Float, gemmini.Float] = GemminiFPConfigs.fpIntConfig(true,w).copy(
      tileRows = 1, tileColumns = 1,
      meshRows = 4, meshColumns = 4,
      sp_capacity = CapacityInKilobytes(32),
      acc_capacity = CapacityInKilobytes(4),
      sp_banks = 4, acc_banks = 1,
      max_in_flight_mem_reqs = 8,
      dma_buswidth = 64, dma_maxbytes = 64,
      tile_latency = 2,
      hardcode_d_to_garbage_addr = true,
      mvin_scale_args = None, mvin_scale_acc_args = None, acc_scale_args = None,
      acc_read_small_width = false, acc_read_full_width = true,
      has_training_convs = false, has_dw_convs = false,
      has_max_pool = false, has_nonlinear_activations = false,
      has_normalizations = false, num_counter = 8)
    LazyModule(new Gemmini(cfg))
  })
})
class GemminiSafeW8Config extends Config(new WithTestSafeGemmini(8) ++ new chipyard.RocketConfig)
class GemminiSafeW4Config extends Config(new WithTestSafeGemmini(4) ++ new chipyard.RocketConfig)
class GemminiSafeW2Config extends Config(new WithTestSafeGemmini(2) ++ new chipyard.RocketConfig)
