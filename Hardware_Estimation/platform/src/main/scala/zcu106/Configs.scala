package chipyard.fpga.zcu106

import java.io.File
import scala.sys.process._

import org.chipsalliance.cde.config.Config
import freechips.rocketchip.devices.tilelink.BootROMLocated
import freechips.rocketchip.resources.DTSTimebase
import freechips.rocketchip.subsystem.SystemBusKey
import sifive.blocks.devices.spi.{PeripherySPIKey, SPIParams}
import sifive.blocks.devices.uart.{PeripheryUARTKey, UARTParams}
import testchipip.serdes.SerialTLKey
import chipyard.ExtTLMem
import LUMAX_PACKAGE.{LUMAXParamsKey, LUMAX_Config}

private object ZCU106Settings {
  def number(name: String, default: Int): Int =
    sys.env.get(name).map(_.toInt).getOrElse(default)

  val clockMHz = number("LUMAX_ZCU106_CLOCK_MHZ", 100)
}

class WithLUMAXZCU106Settings extends Config((site, here, up) => {
  case LUMAXParamsKey =>
    val defaults = LUMAX_Config.LUMAX_Config
    import ZCU106Settings.number
    val ys = number("LUMAX_ZCU106_YS", defaults.y_slice)
    defaults.copy(
      x_slice = number("LUMAX_ZCU106_B", defaults.x_slice),
      Mem_row_factor = number("LUMAX_ZCU106_RF", defaults.Mem_row_factor),
      y_slice = ys,
      Dma_Ids = number("LUMAX_ZCU106_IDS", defaults.Dma_Ids),
      Rin = number("LUMAX_ZCU106_RIN", defaults.Rin.max(ys)),
      Cin = number("LUMAX_ZCU106_CIN", defaults.Cin),
      Cout = number("LUMAX_ZCU106_COUT", defaults.Cout),
      FP16_ACTIVATIONS = sys.env.get("LUMAX_ZCU106_FP16").map(_.toBoolean)
        .getOrElse(defaults.FP16_ACTIVATIONS)
    )
})

class WithZCU106Peripherals extends Config((site, here, up) => {
  case PeripheryUARTKey => List(UARTParams(address = BigInt(0x64000000L)))
  case PeripherySPIKey => List(SPIParams(rAddress = BigInt(0x64001000L)))
  case DTSTimebase => BigInt(1000000)
  case BootROMLocated(x) => up(BootROMLocated(x), site).map { rom =>
    val clockMHz = site(SystemBusKey).dtsFrequency.get / 1000000
    val buildDir = new File(sys.env.getOrElse("LUMAX_ZCU106_BOOT_DIR",
      s"fpga/generated-src/zcu106-sdboot-$clockMHz")).getAbsolutePath
    val chipyard = new File(sys.env.getOrElse("LUMAX_ZCU106_CHIPYARD", ".")).getAbsolutePath
    val command = Seq("make", "-B", "-C", s"$chipyard/fpga/src/main/resources/vcu118/sdboot",
      s"BUILD_DIR=$buildDir", s"PBUS_CLK=$clockMHz", "bin")
    require(command.! == 0, "Failed to build the ZCU106 SD boot ROM")
    rom.copy(hang = 0x10000, contentFileName = s"$buildDir/sdboot.bin")
  }
  case ExtTLMem => up(ExtTLMem, site).map(mem => mem.copy(master =
    mem.master.copy(size = site(ZCU106DDRSize))))
  case SerialTLKey => Nil
})

class WithZCU106Platform extends Config(
  new WithZCU106Peripherals ++
  new chipyard.harness.WithAllClocksFromHarnessClockInstantiator ++
  new chipyard.clocking.WithPassthroughClockGenerator ++
  new chipyard.config.WithUniformBusFrequencies(ZCU106Settings.clockMHz) ++
  new chipyard.harness.WithHarnessBinderClockFreqMHz(ZCU106Settings.clockMHz) ++
  new WithUART ++
  new WithSPISDCard ++
  new WithDDRMem ++
  new chipyard.config.WithTLBackingMemory ++
  new chipyard.config.WithNoDebug ++
  new freechips.rocketchip.subsystem.WithNMemoryChannels(1)
)

class LUMAXzcu106 extends Config(
  new WithLUMAXZCU106Settings ++
  new WithZCU106Platform ++
  new chipyard.LUMAXROcketConfig
)
