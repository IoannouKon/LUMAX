package chipyard.fpga.zcu106

import chisel3._
import chisel3.experimental.dataview._
import freechips.rocketchip.diplomacy._
import freechips.rocketchip.prci._
import org.chipsalliance.cde.config.{Field, Parameters}
import sifive.fpgashells.clocks._
import sifive.fpgashells.shell._
import sifive.fpgashells.shell.xilinx._

// ZCU106 pin assignments and DDR overlay from the Chipyard 1.11.0 board port.
class SysClockZCU106PlacedOverlay(val shell: ZCU106ShellBasicOverlays, name: String, val designInput: ClockInputDesignInput, val shellInput: ClockInputShellInput)
  extends LVDSClockInputXilinxPlacedOverlay(name, designInput, shellInput)
{
  val node = shell { ClockSourceNode(freqMHz = 300, jitterPS = 50)(ValName(name)) }

  shell { InModuleBody {
    shell.xdc.addPackagePin(io.p, "AH12")
    shell.xdc.addPackagePin(io.n, "AJ12")
    shell.xdc.addIOStandard(io.p, "DIFF_SSTL12")
    shell.xdc.addIOStandard(io.n, "DIFF_SSTL12")
  } }
}
class SysClockZCU106ShellPlacer(shell: ZCU106ShellBasicOverlays, val shellInput: ClockInputShellInput)(implicit val valName: ValName)
  extends ClockInputShellPlacer[ZCU106ShellBasicOverlays]
{
    def place(designInput: ClockInputDesignInput) = new SysClockZCU106PlacedOverlay(shell, valName.name, designInput, shellInput)
}

class SDIOZCU106PlacedOverlay(val shell: ZCU106ShellBasicOverlays, name: String, val designInput: SPIDesignInput, val shellInput: SPIShellInput)
  extends SDIOXilinxPlacedOverlay(name, designInput, shellInput)
{
  shell { InModuleBody {
    val packagePinsWithPackageIOs = Seq(("E20", IOPin(io.spi_clk)),
                                        ("A23", IOPin(io.spi_cs)),
                                        ("F25", IOPin(io.spi_dat(0))),
                                        ("K24", IOPin(io.spi_dat(1))),
                                        ("L23", IOPin(io.spi_dat(2))),
                                        ("B23", IOPin(io.spi_dat(3))))

    packagePinsWithPackageIOs foreach { case (pin, io) => {
      shell.xdc.addPackagePin(io, pin)
      shell.xdc.addIOStandard(io, "LVCMOS18")
    } }
    packagePinsWithPackageIOs drop 1 foreach { case (pin, io) => {
      shell.xdc.addPullup(io)
    } }
  } }
}
class SDIOZCU106ShellPlacer(shell: ZCU106ShellBasicOverlays, val shellInput: SPIShellInput)(implicit val valName: ValName)
  extends SPIShellPlacer[ZCU106ShellBasicOverlays] {
  def place(designInput: SPIDesignInput) = new SDIOZCU106PlacedOverlay(shell, valName.name, designInput, shellInput)
}

class UARTZCU106PlacedOverlay(val shell: ZCU106ShellBasicOverlays, name: String, val designInput: UARTDesignInput, val shellInput: UARTShellInput)
  extends UARTXilinxPlacedOverlay(name, designInput, shellInput, false)
{
  shell { InModuleBody {
    val packagePinsWithPackageIOs = Seq(("AH17", IOPin(io.rxd)),
                                        ("AL17", IOPin(io.txd)))

    packagePinsWithPackageIOs foreach { case (pin, io) => {
      shell.xdc.addPackagePin(io, pin)
      shell.xdc.addIOStandard(io, "LVCMOS12")
    } }
  } }
}
class UARTZCU106ShellPlacer(shell: ZCU106ShellBasicOverlays, val shellInput: UARTShellInput)(implicit val valName: ValName)
  extends UARTShellPlacer[ZCU106ShellBasicOverlays] {
  def place(designInput: UARTDesignInput) = new UARTZCU106PlacedOverlay(shell, valName.name, designInput, shellInput)
}

case object ZCU106DDRSize extends Field[BigInt](0x40000000L * 2) // 2GB
class DDRZCU106PlacedOverlay(val shell: ZCU106ShellBasicOverlays, name: String, val designInput: DDRDesignInput, val shellInput: DDRShellInput)
  extends DDRPlacedOverlay[XilinxZCU106MIGPads](name, designInput, shellInput)
{
  val size = p(ZCU106DDRSize)

  val migParams = XilinxZCU106MIGParams(address = AddressSet.misaligned(di.baseAddress, size))
  val mig = LazyModule(new XilinxZCU106MIG(migParams))
  val ddrUI     = shell { ClockSourceNode(freqMHz = 200) }
  val areset    = shell { ClockSinkNode(Seq(ClockSinkParameters())) }
  areset := designInput.wrangler := ddrUI

  def overlayOutput = DDROverlayOutput(ddr = mig.node)
  def ioFactory = new XilinxZCU106MIGPads(size)

  shell { InModuleBody {
    require (shell.sys_clock.get.isDefined, "Use of DDRZCU106Overlay depends on SysClockZCU106Overlay")
    val (sys, _) = shell.sys_clock.get.get.overlayOutput.node.out(0)
    val (ui, _) = ddrUI.out(0)
    val (ar, _) = areset.in(0)
    val port = mig.module.io.port
    io <> port.viewAsSupertype(new ZCU106MIGIODDR(mig.depth))
    ui.clock := port.c0_ddr4_ui_clk
    ui.reset := port.c0_ddr4_ui_clk_sync_rst
    port.c0_sys_clk_i := sys.clock.asUInt
    port.sys_rst := sys.reset
    port.c0_ddr4_aresetn := !(ar.reset.asBool)

    val allddrpins = Seq(
        // A [0:13]
        "AK9", "AG11", "AJ10", "AL8", "AK10", "AH8", "AJ9", "AG8", "AH9", "AG10", "AH13", "AG9", "AM13", "AF8",
        // WE_B, CAS_B, RAS_B, BG0, BA0, BA1
        "AC12", "AE12", "AF11", "AE14", "AK8", "AL12",
        // RESET_B, ACT_B, CK_C, CK_T, CKE, CS_B, ODT
        "AF12", "AD14", "AJ11", "AH11", "AB13", "AD12", "AF10",
        // DQ [0:63]
        "AF16", "AF18", "AG15", "AF17",  "AF15", "AG18", "AG14",  "AE17",  "AA14", "AC16",
        "AB15", "AD16", "AB16", "AC17", "AB14", "AD17", "AJ16", "AJ17", "AL15", "AK17",
        "AJ15", "AK18", "AL16", "AL18", "AP13", "AP16", "AP15", "AN16", "AN13", "AM18",
        "AN17", "AN18", "AB19", "AD19", "AC18", "AC19", "AA20", "AE20", "AA19", "AD20",
        "AF22", "AH21", "AG19", "AG21", "AE24", "AG20", "AE23", "AF21", "AL22", "AJ22",
        "AL23", "AJ21", "AK20", "AJ19", "AK19", "AJ20", "AP22", "AN22", "AP21", "AP23",
        "AM19", "AM23", "AN19", "AN23",
        // DQS_C [0:7]
        "AJ14", "AA15", "AK14", "AN14", "AB18", "AG23", "AK23", "AN21",
        // DQS_T [0:7]
        "AH14", "AA16", "AK15", "AM14", "AA18", "AF23", "AK22", "AM21",
        // DM [0:7]
        "AH18", "AD15", "AM16", "AP18", "AE18", "AH22", "AL20", "AP19")

    require(IOPin.of(io).size == allddrpins.size, "ZCU106 DDR pin count mismatch")
    (IOPin.of(io) zip allddrpins) foreach { case (io, pin) => shell.xdc.addPackagePin(io, pin) }
  } }

  shell.sdc.addGroup(pins = Seq(mig.island.module.blackbox.io.c0_ddr4_ui_clk))
}
class DDRZCU106ShellPlacer(shell: ZCU106ShellBasicOverlays, val shellInput: DDRShellInput)(implicit val valName: ValName)
  extends DDRShellPlacer[ZCU106ShellBasicOverlays] {
  def place(designInput: DDRDesignInput) = new DDRZCU106PlacedOverlay(shell, valName.name, designInput, shellInput)
}

abstract class ZCU106ShellBasicOverlays()(implicit p: Parameters) extends UltraScaleShell {
  val pllReset = InModuleBody { Wire(Bool()) }
  val sys_clock = Overlay(ClockInputOverlayKey, new SysClockZCU106ShellPlacer(this, ClockInputShellInput()))
  val ddr = Overlay(DDROverlayKey, new DDRZCU106ShellPlacer(this, DDRShellInput()))
}
