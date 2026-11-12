package chipyard.fpga.zcu106

import chisel3._

import org.chipsalliance.diplomacy.nodes.{HeterogeneousBag}


import chipyard._
import chipyard.harness._
import chipyard.iobinders._

/*** UART ***/
class WithUART extends HarnessBinder({
  case (th: ZCU106FPGATestHarnessImp, port: UARTPort, chipId: Int) => {
    th.zcu106Outer.io_uart_bb.bundle <> port.io
  }
})

/*** SPI ***/
class WithSPISDCard extends HarnessBinder({
  case (th: ZCU106FPGATestHarnessImp, port: SPIPort, chipId: Int) => {
    th.zcu106Outer.io_spi_bb.bundle <> port.io
  }
})

/*** Experimental DDR ***/
class WithDDRMem extends HarnessBinder({
  case (th: ZCU106FPGATestHarnessImp, port: TLMemPort, chipId: Int) => {
    val bundles = th.zcu106Outer.ddrClient.out.map(_._1)
    val ddrClientBundle = Wire(new HeterogeneousBag(bundles.map(_.cloneType)))
    bundles.zip(ddrClientBundle).foreach { case (bundle, io) => bundle <> io }
    ddrClientBundle <> port.io
  }
})
