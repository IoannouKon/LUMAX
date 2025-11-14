package sifive.fpgashells.shell.xilinx

import chisel3._
import chisel3.experimental.{Analog, attach}
import chisel3.experimental.dataview._
import freechips.rocketchip.diplomacy._
import freechips.rocketchip.prci._
import org.chipsalliance.cde.config._
import sifive.fpgashells.clocks._
import sifive.fpgashells.devices.xilinx.xdma._
import sifive.fpgashells.devices.xilinx.xilinxzcu106mig._
import sifive.fpgashells.ip.xilinx._
import sifive.fpgashells.ip.xilinx.xxv_ethernet._
import sifive.fpgashells.ip.xilinx.zcu106mig._
import sifive.fpgashells.shell._

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

class RefClockZCU106PlacedOverlay(val shell: ZCU106ShellBasicOverlays, name: String, val designInput: ClockInputDesignInput, val shellInput: ClockInputShellInput)
  extends LVDSClockInputXilinxPlacedOverlay(name, designInput, shellInput) {
  val node = shell { ClockSourceNode(freqMHz = 125, jitterPS = 50)(ValName(name)) }

  shell { InModuleBody {
    shell.xdc.addPackagePin(io.p, "H9")
    shell.xdc.addPackagePin(io.n, "G9")
    shell.xdc.addIOStandard(io.p, "LVDS")
    shell.xdc.addIOStandard(io.n, "LVDS")
  } }
}
class RefClockZCU106ShellPlacer(shell: ZCU106ShellBasicOverlays, val shellInput: ClockInputShellInput)(implicit val valName: ValName)
  extends ClockInputShellPlacer[ZCU106ShellBasicOverlays] {
  def place(designInput: ClockInputDesignInput) = new RefClockZCU106PlacedOverlay(shell, valName.name, designInput, shellInput)
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
      shell.xdc.addIOB(io)
    } }
  } }
}
class SDIOZCU106ShellPlacer(shell: ZCU106ShellBasicOverlays, val shellInput: SPIShellInput)(implicit val valName: ValName)
  extends SPIShellPlacer[ZCU106ShellBasicOverlays] {
  def place(designInput: SPIDesignInput) = new SDIOZCU106PlacedOverlay(shell, valName.name, designInput, shellInput)
}

class SPIFlashZCU106PlacedOverlay(val shell: ZCU106ShellBasicOverlays, name: String, val designInput: SPIFlashDesignInput, val shellInput: SPIFlashShellInput)
  extends SPIFlashXilinxPlacedOverlay(name, designInput, shellInput)
{

  shell { InModuleBody {
    /*val packagePinsWithPackageIOs = Seq(("AF13", IOPin(io.qspi_sck)),
      ("AJ11", IOPin(io.qspi_cs)),
      ("AP11", IOPin(io.qspi_dq(0))),
      ("AN11", IOPin(io.qspi_dq(1))),
      ("AM11", IOPin(io.qspi_dq(2))),
      ("AL11", IOPin(io.qspi_dq(3))))

    packagePinsWithPackageIOs foreach { case (pin, io) => {
      shell.xdc.addPackagePin(io, pin)
      shell.xdc.addIOStandard(io, "LVCMOS18")
      shell.xdc.addIOB(io)
    } }
    packagePinsWithPackageIOs drop 1 foreach { case (pin, io) => {
      shell.xdc.addPullup(io)
    } }
*/
  } }
}
class SPIFlashZCU106ShellPlacer(shell: ZCU106ShellBasicOverlays, val shellInput: SPIFlashShellInput)(implicit val valName: ValName)
  extends SPIFlashShellPlacer[ZCU106ShellBasicOverlays] {
  def place(designInput: SPIFlashDesignInput) = new SPIFlashZCU106PlacedOverlay(shell, valName.name, designInput, shellInput)
}

class UARTZCU106PlacedOverlay(val shell: ZCU106ShellBasicOverlays, name: String, val designInput: UARTDesignInput, val shellInput: UARTShellInput)
  extends UARTXilinxPlacedOverlay(name, designInput, shellInput, true)
{
  shell { InModuleBody {
    val packagePinsWithPackageIOs = Seq(("AM15", IOPin(io.ctsn.get)),
                                        ("AP17", IOPin(io.rtsn.get)),
                                        ("AH17", IOPin(io.rxd)),
                                        ("AL17", IOPin(io.txd)))

    packagePinsWithPackageIOs foreach { case (pin, io) => {
      shell.xdc.addPackagePin(io, pin)
      shell.xdc.addIOStandard(io, "LVCMOS12")
      shell.xdc.addIOB(io)
    } }
  } }
}
class UARTZCU106ShellPlacer(shell: ZCU106ShellBasicOverlays, val shellInput: UARTShellInput)(implicit val valName: ValName)
  extends UARTShellPlacer[ZCU106ShellBasicOverlays] {
  def place(designInput: UARTDesignInput) = new UARTZCU106PlacedOverlay(shell, valName.name, designInput, shellInput)
}

class QSFP1ZCU106PlacedOverlay(val shell: ZCU106ShellBasicOverlays, name: String, val designInput: EthernetDesignInput, val shellInput: EthernetShellInput)
  extends EthernetUltraScalePlacedOverlay(name, designInput, shellInput, XXVEthernetParams(name = name, speed   = 10, dclkMHz = 125))
{
  val dclkSource = shell { BundleBridgeSource(() => Clock()) }
  val dclkSink = dclkSource.makeSink()
  InModuleBody {
    dclk := dclkSink.bundle
  }
  shell { InModuleBody {
    dclkSource.bundle := shell.ref_clock.get.get.overlayOutput.node.out(0)._1.clock
    shell.xdc.addPackagePin(io.tx_p, "Y4")
    shell.xdc.addPackagePin(io.tx_n, "Y3")
    shell.xdc.addPackagePin(io.rx_p, "AA2")
    shell.xdc.addPackagePin(io.rx_n, "AA1")
    shell.xdc.addPackagePin(io.refclk_p, "W10")
    shell.xdc.addPackagePin(io.refclk_n, "W9")
  } }
}
class QSFP1ZCU106ShellPlacer(shell: ZCU106ShellBasicOverlays, val shellInput: EthernetShellInput)(implicit val valName: ValName)
  extends EthernetShellPlacer[ZCU106ShellBasicOverlays] {
  def place(designInput: EthernetDesignInput) = new QSFP1ZCU106PlacedOverlay(shell, valName.name, designInput, shellInput)
}

class QSFP2ZCU106PlacedOverlay(val shell: ZCU106ShellBasicOverlays, name: String, val designInput: EthernetDesignInput, val shellInput: EthernetShellInput)
  extends EthernetUltraScalePlacedOverlay(name, designInput, shellInput, XXVEthernetParams(name = name, speed   = 10, dclkMHz = 125))
{
  val dclkSource = shell { BundleBridgeSource(() => Clock()) }
  val dclkSink = dclkSource.makeSink()
  InModuleBody {
    dclk := dclkSink.bundle
  }
  shell { InModuleBody {
    dclkSource.bundle := shell.ref_clock.get.get.overlayOutput.node.out(0)._1.clock
    shell.xdc.addPackagePin(io.tx_p, "W6")
    shell.xdc.addPackagePin(io.tx_n, "W5")
    shell.xdc.addPackagePin(io.rx_p, "W2")
    shell.xdc.addPackagePin(io.rx_n, "W1")
    shell.xdc.addPackagePin(io.refclk_p, "R10")
    shell.xdc.addPackagePin(io.refclk_n, "R9")
  } }
}
class QSFP2ZCU106ShellPlacer(shell: ZCU106ShellBasicOverlays, val shellInput: EthernetShellInput)(implicit val valName: ValName)
  extends EthernetShellPlacer[ZCU106ShellBasicOverlays] {
  def place(designInput: EthernetDesignInput) = new QSFP2ZCU106PlacedOverlay(shell, valName.name, designInput, shellInput)
}

object LEDZCU106PinConstraints {
  val pins = Seq("AL11", "AL13", "AK13", "AE15", "AM8", "AM9", "AM10", "AM11")
}
class LEDZCU106PlacedOverlay(val shell: ZCU106ShellBasicOverlays, name: String, val designInput: LEDDesignInput, val shellInput: LEDShellInput)
  extends LEDXilinxPlacedOverlay(name, designInput, shellInput, packagePin = Some(LEDZCU106PinConstraints.pins(shellInput.number)), ioStandard = "LVCMOS12")
class LEDZCU106ShellPlacer(shell: ZCU106ShellBasicOverlays, val shellInput: LEDShellInput)(implicit val valName: ValName)
  extends LEDShellPlacer[ZCU106ShellBasicOverlays] {
  def place(designInput: LEDDesignInput) = new LEDZCU106PlacedOverlay(shell, valName.name, designInput, shellInput)
}

object ButtonZCU106PinConstraints {
  val pins = Seq("AG13", "AC14", "AK12", "AP20", "AL10")
}
class ButtonZCU106PlacedOverlay(val shell: ZCU106ShellBasicOverlays, name: String, val designInput: ButtonDesignInput, val shellInput: ButtonShellInput)
  extends ButtonXilinxPlacedOverlay(name, designInput, shellInput, packagePin = Some(ButtonZCU106PinConstraints.pins(shellInput.number)), ioStandard = "LVCMOS18")
class ButtonZCU106ShellPlacer(shell: ZCU106ShellBasicOverlays, val shellInput: ButtonShellInput)(implicit val valName: ValName)
  extends ButtonShellPlacer[ZCU106ShellBasicOverlays] {
  def place(designInput: ButtonDesignInput) = new ButtonZCU106PlacedOverlay(shell, valName.name, designInput, shellInput)
}

object SwitchZCU106PinConstraints {
  val pins = Seq("A16", "B16", "B15", "A15")
}
class SwitchZCU106PlacedOverlay(val shell: ZCU106ShellBasicOverlays, name: String, val designInput: SwitchDesignInput, val shellInput: SwitchShellInput)
  extends SwitchXilinxPlacedOverlay(name, designInput, shellInput, packagePin = Some(SwitchZCU106PinConstraints.pins(shellInput.number)), ioStandard = "LVCMOS12")
class SwitchZCU106ShellPlacer(shell: ZCU106ShellBasicOverlays, val shellInput: SwitchShellInput)(implicit val valName: ValName)
  extends SwitchShellPlacer[ZCU106ShellBasicOverlays] {
  def place(designInput: SwitchDesignInput) = new SwitchZCU106PlacedOverlay(shell, valName.name, designInput, shellInput)
}

class ChipLinkZCU106PlacedOverlay(val shell: ZCU106ShellBasicOverlays, name: String, val designInput: ChipLinkDesignInput, val shellInput: ChipLinkShellInput)
  extends ChipLinkXilinxPlacedOverlay(name, designInput, shellInput, rxPhase= -120, txPhase= -90, rxMargin=0.6, txMargin=0.5)
{
  val ereset_n = shell { InModuleBody {
    val ereset_n = IO(Analog(1.W))
    ereset_n.suggestName("ereset_n")
    val pin = IOPin(ereset_n, 0)
    shell.xdc.addPackagePin(pin, "E14")
    shell.xdc.addIOStandard(pin, "LVDS")
    shell.xdc.addTermination(pin, "NONE")
    shell.xdc.addPullup(pin)

    val iobuf = Module(new IOBUF)
    iobuf.suggestName("chiplink_ereset_iobuf")
    attach(ereset_n, iobuf.io.IO)
    iobuf.io.T := true.B // !oe
    iobuf.io.I := false.B

    iobuf.io.O
  } }

  shell { InModuleBody {
    val dir1 = Seq("E15", "C17", "D17", /* clk, rst, send */
                "F17",  "F16",  "H18", "H17",  "L20", "K20", "K19", "J22",
                "L17", "L16", "K17", "J17", "H19", "G19", "J16", "J15",
                "E18", "E17", "H16", "G16", "L15", "K15", "A13", "A12",
                "G18", "F18", "G15",  "F15",  "C13",  "C12",  "D16", "C16")

    val dir2 = Seq("G10", "C8", "C9", /* clk, rst, send */
                "F11", "E10", "D11", "D10", "D12", "C11", "F12", "E12",
                "B10", "A10", "H13", "H12", "B11", "A11", "B6", "A6",
                "C7", "C6", "B9", "B8", "A8", "A7", "M13", "L13",
                "K10", "J10", "E9", "D9", "F7", "E7", "F8", "E8")
    (IOPin.of(io.b2c) zip dir1) foreach { case (io, pin) => shell.xdc.addPackagePin(io, pin) }
    (IOPin.of(io.c2b) zip dir2) foreach { case (io, pin) => shell.xdc.addPackagePin(io, pin) }
  } }
}
class ChipLinkZCU106ShellPlacer(shell: ZCU106ShellBasicOverlays, val shellInput: ChipLinkShellInput)(implicit val valName: ValName)
  extends ChipLinkShellPlacer[ZCU106ShellBasicOverlays] {
  def place(designInput: ChipLinkDesignInput) = new ChipLinkZCU106PlacedOverlay(shell, valName.name, designInput, shellInput)
}

// TODO: JTAG is untested
class JTAGDebugZCU106PlacedOverlay(val shell: ZCU106ShellBasicOverlays, name: String, val designInput: JTAGDebugDesignInput, val shellInput: JTAGDebugShellInput)
  extends JTAGDebugXilinxPlacedOverlay(name, designInput, shellInput)
{
  shell { InModuleBody {
    val pin_locations = Map(
      "PMOD_J55" -> Seq("F25", "L23", "K24", "B23", "A23"),
      "PMOD_J87" -> Seq("AP11", "AP10", "AP9", "AN8", "AN9"),
      "FMC_J4"   -> Seq("D9", "K10", "J10", "E7", "E9"))
    val pins      = Seq(io.jtag_TCK, io.jtag_TMS, io.jtag_TDI, io.jtag_TDO, io.srst_n)

    shell.sdc.addClock("JTCK", IOPin(io.jtag_TCK), 10)
    shell.sdc.addGroup(clocks = Seq("JTCK"))
    shell.xdc.clockDedicatedRouteFalse(IOPin(io.jtag_TCK))

    val pin_voltage:String = if(shellInput.location.get == "PMOD_J87") "LVCMOS12" else "LVCMOS18"

    (pin_locations(shellInput.location.get) zip pins) foreach { case (pin_location, ioport) =>
      val io = IOPin(ioport)
      shell.xdc.addPackagePin(io, pin_location)
      shell.xdc.addIOStandard(io, pin_voltage)
      shell.xdc.addPullup(io)
      shell.xdc.addIOB(io)
    }
  } }
}
class JTAGDebugZCU106ShellPlacer(shell: ZCU106ShellBasicOverlays, val shellInput: JTAGDebugShellInput)(implicit val valName: ValName)
  extends JTAGDebugShellPlacer[ZCU106ShellBasicOverlays] {
  def place(designInput: JTAGDebugDesignInput) = new JTAGDebugZCU106PlacedOverlay(shell, valName.name, designInput, shellInput)
}

class cJTAGDebugZCU106PlacedOverlay(val shell: ZCU106ShellBasicOverlays, name: String, val designInput: cJTAGDebugDesignInput, val shellInput: cJTAGDebugShellInput)
  extends cJTAGDebugXilinxPlacedOverlay(name, designInput, shellInput)
{
  shell { InModuleBody {
    shell.sdc.addClock("JTCKC", IOPin(io.cjtag_TCKC), 10)
    shell.sdc.addGroup(clocks = Seq("JTCKC"))
    shell.xdc.clockDedicatedRouteFalse(IOPin(io.cjtag_TCKC))
    val packagePinsWithPackageIOs = Seq(("F12", IOPin(io.cjtag_TCKC)),
                                        ("B6", IOPin(io.cjtag_TMSC)),
                                        ("E12", IOPin(io.srst_n)))

    packagePinsWithPackageIOs foreach { case (pin, io) => {
      shell.xdc.addPackagePin(io, pin)
      shell.xdc.addIOStandard(io, "LVDS")
    } }
      shell.xdc.addPullup(IOPin(io.cjtag_TCKC))
      shell.xdc.addPullup(IOPin(io.srst_n))
  } }
}
class cJTAGDebugZCU106ShellPlacer(shell: ZCU106ShellBasicOverlays, val shellInput: cJTAGDebugShellInput)(implicit val valName: ValName)
  extends cJTAGDebugShellPlacer[ZCU106ShellBasicOverlays] {
  def place(designInput: cJTAGDebugDesignInput) = new cJTAGDebugZCU106PlacedOverlay(shell, valName.name, designInput, shellInput)
}

class JTAGDebugBScanZCU106PlacedOverlay(val shell: ZCU106ShellBasicOverlays, name: String, val designInput: JTAGDebugBScanDesignInput, val shellInput: JTAGDebugBScanShellInput)
  extends JTAGDebugBScanXilinxPlacedOverlay(name, designInput, shellInput)
class JTAGDebugBScanZCU106ShellPlacer(val shell: ZCU106ShellBasicOverlays, val shellInput: JTAGDebugBScanShellInput)(implicit val valName: ValName)
  extends JTAGDebugBScanShellPlacer[ZCU106ShellBasicOverlays] {
  def place(designInput: JTAGDebugBScanDesignInput) = new JTAGDebugBScanZCU106PlacedOverlay(shell, valName.name, designInput, shellInput)
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
    ui.reset := /*!port.mmcm_locked ||*/ port.c0_ddr4_ui_clk_sync_rst
    port.c0_sys_clk_i := sys.clock.asUInt
    port.sys_rst := sys.reset // pllReset
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

    (IOPin.of(io) zip allddrpins) foreach { case (io, pin) => shell.xdc.addPackagePin(io, pin) }
  } }

  shell.sdc.addGroup(pins = Seq(mig.island.module.blackbox.io.c0_ddr4_ui_clk))
}
class DDRZCU106ShellPlacer(shell: ZCU106ShellBasicOverlays, val shellInput: DDRShellInput)(implicit val valName: ValName)
  extends DDRShellPlacer[ZCU106ShellBasicOverlays] {
  def place(designInput: DDRDesignInput) = new DDRZCU106PlacedOverlay(shell, valName.name, designInput, shellInput)
}

// class PCIeZCU106FMCPlacedOverlay(val shell: ZCU106ShellBasicOverlays, name: String, val designInput: PCIeDesignInput, val shellInput: PCIeShellInput)
//   extends PCIeUltraScalePlacedOverlay(name, designInput, shellInput, XDMAParams(
//     name     = "fmc_xdma",
//     location = "X0Y3",
//     bars     = designInput.bars,
//     control  = designInput.ecam,
//     bases    = designInput.bases,
//     lanes    = 4))
// {
//   shell { InModuleBody {
//     // Work-around incorrectly pre-assigned pins
//     IOPin.of(io).foreach { shell.xdc.addPackagePin(_, "") }

//     // We need some way to connect both of these to reach x8
//     val ref126 = Seq("V38",  "V39")  /* [pn] GBT0 Bank 126 */
//     val ref121 = Seq("AK38", "AK39") /* [pn] GBT0 Bank 121 */
//     val ref = ref126

//     // Bank 126 (DP5, DP6, DP4, DP7), Bank 121 (DP3, DP2, DP1, DP0)
//     val rxp = Seq("U45", "R45", "W45", "N45", "AJ45", "AL45", "AN45", "AR45") /* [0-7] */
//     val rxn = Seq("U46", "R46", "W46", "N46", "AJ46", "AL46", "AN46", "AR46") /* [0-7] */
//     val txp = Seq("P42", "M42", "T42", "K42", "AL40", "AM42", "AP42", "AT42") /* [0-7] */
//     val txn = Seq("P43", "M43", "T43", "K43", "AL41", "AM43", "AP43", "AT43") /* [0-7] */

//     def bind(io: Seq[IOPin], pad: Seq[String]) {
//       (io zip pad) foreach { case (io, pad) => shell.xdc.addPackagePin(io, pad) }
//     }

//     bind(IOPin.of(io.refclk), ref)
//     // We do these individually so that zip falls off the end of the lanes:
//     bind(IOPin.of(io.lanes.pci_exp_txp), txp)
//     bind(IOPin.of(io.lanes.pci_exp_txn), txn)
//     bind(IOPin.of(io.lanes.pci_exp_rxp), rxp)
//     bind(IOPin.of(io.lanes.pci_exp_rxn), rxn)
//   } }
// }
// class PCIeZCU106FMCShellPlacer(shell: ZCU106ShellBasicOverlays, val shellInput: PCIeShellInput)(implicit val valName: ValName)
//   extends PCIeShellPlacer[ZCU106ShellBasicOverlays] {
//   def place(designInput: PCIeDesignInput) = new PCIeZCU106FMCPlacedOverlay(shell, valName.name, designInput, shellInput)
// }

// class PCIeZCU106EdgePlacedOverlay(val shell: ZCU106ShellBasicOverlays, name: String, val designInput: PCIeDesignInput, val shellInput: PCIeShellInput)
//   extends PCIeUltraScalePlacedOverlay(name, designInput, shellInput, XDMAParams(
//     name     = "edge_xdma",
//     location = "X1Y2",
//     bars     = designInput.bars,
//     control  = designInput.ecam,
//     bases    = designInput.bases,
//     lanes    = 8))
// {
//   shell { InModuleBody {
//     // Work-around incorrectly pre-assigned pins
//     IOPin.of(io).foreach { shell.xdc.addPackagePin(_, "") }

//     // PCIe Edge connector U2
//     //   Lanes 00-03 Bank 227
//     //   Lanes 04-07 Bank 226
//     //   Lanes 08-11 Bank 225
//     //   Lanes 12-15 Bank 224

//     // FMC+ J42
//     val ref227 = Seq("AC9", "AC8")  /* [pn]  Bank 227 PCIE_CLK2_*/
//     val ref = ref227

//     // PCIe Edge connector U2 : Bank 227, 226
//     val rxp = Seq("AA4", "AB2", "AC4", "AD2", "AE4", "AF2", "AG4", "AH2") // [0-7]
//     val rxn = Seq("AA3", "AB1", "AC3", "AD1", "AE3", "AF1", "AG3", "AH1") // [0-7]
//     val txp = Seq("Y7", "AB7", "AD7", "AF7", "AH7", "AK7", "AM7", "AN5") // [0-7]
//     val txn = Seq("Y6", "AB6", "AD6", "AF6", "AH6", "AK6", "AM6", "AN4") // [0-7]

//     def bind(io: Seq[IOPin], pad: Seq[String]) {
//       (io zip pad) foreach { case (io, pad) => shell.xdc.addPackagePin(io, pad) }
//     }

//     bind(IOPin.of(io.refclk), ref)
//     // We do these individually so that zip falls off the end of the lanes:
//     bind(IOPin.of(io.lanes.pci_exp_txp), txp)
//     bind(IOPin.of(io.lanes.pci_exp_txn), txn)
//     bind(IOPin.of(io.lanes.pci_exp_rxp), rxp)
//     bind(IOPin.of(io.lanes.pci_exp_rxn), rxn)
//   } }
// }
// class PCIeZCU106EdgeShellPlacer(shell: ZCU106ShellBasicOverlays, val shellInput: PCIeShellInput)(implicit val valName: ValName)
//   extends PCIeShellPlacer[ZCU106ShellBasicOverlays] {
//   def place(designInput: PCIeDesignInput) = new PCIeZCU106EdgePlacedOverlay(shell, valName.name, designInput, shellInput)
// }

abstract class ZCU106ShellBasicOverlays()(implicit p: Parameters) extends UltraScaleShell{
  // PLL reset causes
  val pllReset = InModuleBody { Wire(Bool()) }

  val sys_clock = Overlay(ClockInputOverlayKey, new SysClockZCU106ShellPlacer(this, ClockInputShellInput()))
  val ref_clock = Overlay(ClockInputOverlayKey, new RefClockZCU106ShellPlacer(this, ClockInputShellInput()))
  val led       = Seq.tabulate(8)(i => Overlay(LEDOverlayKey, new LEDZCU106ShellPlacer(this, LEDShellInput(color = "red", number = i))(valName = ValName(s"led_$i"))))
  val switch    = Seq.tabulate(4)(i => Overlay(SwitchOverlayKey, new SwitchZCU106ShellPlacer(this, SwitchShellInput(number = i))(valName = ValName(s"switch_$i"))))
  val button    = Seq.tabulate(5)(i => Overlay(ButtonOverlayKey, new ButtonZCU106ShellPlacer(this, ButtonShellInput(number = i))(valName = ValName(s"button_$i"))))
  val ddr       = Overlay(DDROverlayKey, new DDRZCU106ShellPlacer(this, DDRShellInput()))
  val qsfp1     = Overlay(EthernetOverlayKey, new QSFP1ZCU106ShellPlacer(this, EthernetShellInput()))
  val qsfp2     = Overlay(EthernetOverlayKey, new QSFP2ZCU106ShellPlacer(this, EthernetShellInput()))
  val chiplink  = Overlay(ChipLinkOverlayKey, new ChipLinkZCU106ShellPlacer(this, ChipLinkShellInput()))
  //val spi_flash = Overlay(SPIFlashOverlayKey, new SPIFlashZCU106ShellPlacer(this, SPIFlashShellInput()))
  //SPI Flash not functional
}

case object ZCU106ShellPMOD extends Field[String]("JTAG")
case object ZCU106ShellPMOD2 extends Field[String]("JTAG")

class WithZCU106ShellPMOD(device: String) extends Config((site, here, up) => {
  case ZCU106ShellPMOD => device
})

// Change JTAG pinouts to ZCU106 J87
// Due to the level shifter is from 1.2V to 3.3V, the frequency of JTAG should be slow down to 1Mhz
class WithZCU106ShellPMOD2(device: String) extends Config((site, here, up) => {
  case ZCU106ShellPMOD2 => device
})

class WithZCU106ShellPMODJTAG extends WithZCU106ShellPMOD("JTAG")
class WithZCU106ShellPMODSDIO extends WithZCU106ShellPMOD("SDIO")

// Reassign JTAG pinouts location to PMOD J87
class WithZCU106ShellPMOD2JTAG extends WithZCU106ShellPMOD2("PMODJ87_JTAG")

class ZCU106Shell()(implicit p: Parameters) extends ZCU106ShellBasicOverlays
{
  val pmod_is_sdio  = p(ZCU106ShellPMOD) == "SDIO"
  val pmod_j87_is_jtag = p(ZCU106ShellPMOD2) == "PMODJ87_JTAG"
  val jtag_location = Some(if (pmod_is_sdio) (if (pmod_j87_is_jtag) "PMOD_J87" else "FMC_J4") else "PMOD_J55")

  // Order matters; ddr depends on sys_clock
  val uart      = Overlay(UARTOverlayKey, new UARTZCU106ShellPlacer(this, UARTShellInput()))
  val sdio      = if (pmod_is_sdio) Some(Overlay(SPIOverlayKey, new SDIOZCU106ShellPlacer(this, SPIShellInput()))) else None
  // val jtag      = Overlay(JTAGDebugOverlayKey, new JTAGDebugZCU106ShellPlacer(this, JTAGDebugShellInput(location = jtag_location)))
  // val cjtag     = Overlay(cJTAGDebugOverlayKey, new cJTAGDebugZCU106ShellPlacer(this, cJTAGDebugShellInput()))
  // val jtagBScan = Overlay(JTAGDebugBScanOverlayKey, new JTAGDebugBScanZCU106ShellPlacer(this, JTAGDebugBScanShellInput()))
  // val fmc       = Overlay(PCIeOverlayKey, new PCIeZCU106FMCShellPlacer(this, PCIeShellInput()))
  // val edge      = Overlay(PCIeOverlayKey, new PCIeZCU106EdgeShellPlacer(this, PCIeShellInput()))

  val topDesign = LazyModule(p(DesignKey)(designParameters))

  // Place the sys_clock at the Shell if the user didn't ask for it
  designParameters(ClockInputOverlayKey).foreach { unused =>
    val source = unused.place(ClockInputDesignInput()).overlayOutput.node
    val sink = ClockSinkNode(Seq(ClockSinkParameters()))
    sink := source
  }

  override lazy val module = new LazyRawModuleImp(this) {
    val reset = IO(Input(Bool()))
    xdc.addPackagePin(reset, "G13")
    xdc.addIOStandard(reset, "LVCMOS18")

    val reset_ibuf = Module(new IBUF)
    reset_ibuf.io.I := reset

    val sysclk: Clock = sys_clock.get() match {
      case Some(x: SysClockZCU106PlacedOverlay) => x.clock
    }

    val powerOnReset: Bool = PowerOnResetFPGAOnly(sysclk)
    sdc.addAsyncPath(Seq(powerOnReset))

    val ereset: Bool = chiplink.get() match {
      case Some(x: ChipLinkZCU106PlacedOverlay) => !x.ereset_n
      case _ => false.B
    }

    pllReset := (reset_ibuf.io.O || powerOnReset || ereset)
  }
}

/*
   Copyright 2016 SiFive, Inc.

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
*/
