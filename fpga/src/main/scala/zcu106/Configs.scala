package chipyard.fpga.zcu106

import sys.process._

import org.chipsalliance.cde.config.{Config, Parameters}
import freechips.rocketchip.subsystem.{SystemBusKey, PeripheryBusKey, ControlBusKey, ExtMem}
import freechips.rocketchip.devices.debug.{DebugModuleKey, ExportDebug, JTAG}
import freechips.rocketchip.devices.tilelink.{DevNullParams, BootROMLocated}
import freechips.rocketchip.diplomacy.{DTSModel, DTSTimebase, RegionType, AddressSet}
import freechips.rocketchip.tile.{XLen}

import sifive.blocks.devices.spi.{PeripherySPIKey, SPIParams}
import sifive.blocks.devices.uart.{PeripheryUARTKey, UARTParams}

import sifive.fpgashells.shell.{DesignKey}
import sifive.fpgashells.shell.xilinx.{ZCU106ShellPMOD, ZCU106DDRSize}

import testchipip.serdes.{SerialTLKey}

import chipyard._
import chipyard.harness._

import freechips.rocketchip.diplomacy.{AsynchronousCrossing}


// import sys.process._

// import org.chipsalliance.cde.config.{Config, Parameters}
// import freechips.rocketchip.subsystem.{SystemBusKey, PeripheryBusKey, ControlBusKey, ExtMem}
// import freechips.rocketchip.devices.debug.{DebugModuleKey, ExportDebug, JTAG}
// import freechips.rocketchip.devices.tilelink.{DevNullParams, BootROMLocated}
// import freechips.rocketchip.diplomacy.{RegionType, AddressSet}
// import freechips.rocketchip.resources.{DTSModel, DTSTimebase}

// import sifive.blocks.devices.spi.{PeripherySPIKey, SPIParams}
// import sifive.blocks.devices.uart.{PeripheryUARTKey, UARTParams}

// import sifive.fpgashells.shell.{DesignKey}
// import sifive.fpgashells.shell.xilinx.{ZCU106ShellPMOD, ZCU106DDRSize}

// import testchipip.serdes.{SerialTLKey}

// import chipyard._
// import chipyard.harness._

// import freechips.rocketchip.prci.{AsynchronousCrossing}

class WithDefaultPeripherals extends Config((site, here, up) => {
  case PeripheryUARTKey => List(UARTParams(address = BigInt(0x64000000L)))
  case PeripherySPIKey => List(SPIParams(rAddress = BigInt(0x64001000L)))
  case ZCU106ShellPMOD => "SDIO"
})

class WithSystemModifications extends Config((site, here, up) => {
  case DTSTimebase => BigInt((1e6).toLong)
  case BootROMLocated(x) => up(BootROMLocated(x), site).map { p =>
    // invoke makefile for sdboot
    val freqMHz = (site(SystemBusKey).dtsFrequency.get / (1000 * 1000)).toLong
    val make = s"make -C fpga/src/main/resources/zcu106/sdboot PBUS_CLK=${freqMHz} bin"
    require (make.! == 0, "Failed to build bootrom")
    p.copy(hang = 0x10000, contentFileName = s"./fpga/src/main/resources/zcu106/sdboot/build/sdboot.bin")
  }
  case ExtMem => up(ExtMem, site).map(x => x.copy(master = x.master.copy(size = site(ZCU106DDRSize)))) // set extmem to DDR size
  case SerialTLKey => Nil // remove serialized tl port
})

class WithZCU106Tweaks extends Config(
  new chipyard.harness.WithAllClocksFromHarnessClockInstantiator ++
  new chipyard.clocking.WithPassthroughClockGenerator ++
  new chipyard.config.WithMemoryBusFrequency(100) ++
  new chipyard.config.WithSystemBusFrequency(100) ++
  new chipyard.config.WithControlBusFrequency(100) ++
  new chipyard.config.WithPeripheryBusFrequency(100) ++
  new chipyard.config.WithControlBusFrequency(100) ++
  new chipyard.config.WithFrontBusFrequency(100)++
  // new chipyard.config.WithUniformBusFrequencies(100) ++
  new WithFPGAFrequency(100) ++ // default 100MHz freq
 // new WithJTAG ++  //George had it. I removed it cause I added WithNoDebug
  // harness binders
  new WithUART ++
  new WithSPISDCard ++
  new WithDDRMem ++
  // other configuration
  new WithDefaultPeripherals ++
  new chipyard.config.WithTLBackingMemory ++ // use TL backing memory
  new WithSystemModifications ++ // setup busses, use sdboot bootrom, setup ext. mem. size
  new chipyard.config.WithNoDebug ++ // remove debug module
  new freechips.rocketchip.subsystem.WithoutTLMonitors ++
  new freechips.rocketchip.subsystem.WithNMemoryChannels(1) 
)

// Rocket Configs found in /home/riscv/Documents/Chipyard/latest/generators/rocket-chip/src/main/scala/rocket/Configs.scala
// freechips.rocketchip.rocket.WithNHugeCores
// freechips.rocketchip.rocket.WithNBigCores
// freechips.rocketchip.rocket.WithNMedCores
// freechips.rocketchip.rocket.WithNSmallCores
// Boom Configs found in /home/riscv/Documents/Chipyard/latest/generators/boom/src/main/scala/v4/common/config-mixins.scala
// boom.v4.common.WithNSmallBooms
// boom.v4.common.WithNMediumBooms
// boom.v4.common.WithNLargeBooms
// boom.v4.common.WithNMegaBooms
// boom.v4.common.WithNGigaBooms

class SmallRocket extends Config(
  new chipyard.harness.WithAllClocksFromHarnessClockInstantiator ++
  new chipyard.clocking.WithPassthroughClockGenerator ++
  //new chipyard.config.WithUniformBusFrequencies(100) ++
  new WithFPGAFrequency(100) ++
  new WithZCU106Tweaks ++
  new freechips.rocketchip.subsystem.WithNSmallCores(1) ++
  new chipyard.config.AbstractConfig
)

class MediumRocket extends Config(
  new chipyard.harness.WithAllClocksFromHarnessClockInstantiator ++
  new chipyard.clocking.WithPassthroughClockGenerator ++
  //new chipyard.config.WithUniformBusFrequencies(100) ++
  new WithFPGAFrequency(100) ++
  new WithZCU106Tweaks ++
  new freechips.rocketchip.subsystem.WithNMedCores(1) ++
  new chipyard.config.AbstractConfig
)

class BigRocket extends Config(
  new chipyard.harness.WithAllClocksFromHarnessClockInstantiator ++
  new chipyard.clocking.WithPassthroughClockGenerator ++
  //new chipyard.config.WithUniformBusFrequencies(100) ++
  new WithFPGAFrequency(100) ++
  new WithZCU106Tweaks ++
  new freechips.rocketchip.subsystem.WithNBigCores(1) ++
  new chipyard.config.AbstractConfig
)

class HugeRocket extends Config(
  new chipyard.harness.WithAllClocksFromHarnessClockInstantiator ++
  new chipyard.clocking.WithPassthroughClockGenerator ++
  //new chipyard.config.WithUniformBusFrequencies(125) ++
  new WithFPGAFrequency(125) ++
  new WithZCU106Tweaks ++
  //new freechips.rocketchip.subsystem.WithNHugeCores(1) ++
  new chipyard.config.AbstractConfig
)

// class SmallBoom extends Config(
//   new chipyard.harness.WithAllClocksFromHarnessClockInstantiator ++
//   new chipyard.clocking.WithPassthroughClockGenerator ++
//   //new chipyard.config.WithUniformBusFrequencies(100) ++
//   new WithFPGAFrequency(100) ++
//   new WithZCU106Tweaks ++
//   new boom.v4.common.WithNSmallBooms(1) ++
//   new chipyard.config.AbstractConfig
// )

// class MediumBoom extends Config(
//   new chipyard.harness.WithAllClocksFromHarnessClockInstantiator ++
//   new chipyard.clocking.WithPassthroughClockGenerator ++
//   //new chipyard.config.WithUniformBusFrequencies(150) ++
//   new WithFPGAFrequency(150) ++
//   new WithZCU106Tweaks ++
//   new boom.v4.common.WithNMediumBooms(1) ++
//   new chipyard.config.AbstractConfig
// )

// class LargeBoom extends Config(
//   new chipyard.harness.WithAllClocksFromHarnessClockInstantiator ++
//   new chipyard.clocking.WithPassthroughClockGenerator ++
//   //new chipyard.config.WithUniformBusFrequencies(100) ++
//   new WithFPGAFrequency(100) ++
//   new WithZCU106Tweaks ++
//   new boom.v4.common.WithNLargeBooms(1) ++
//   new chipyard.config.AbstractConfig
// )

// class MegaBoom extends Config(
//   new chipyard.harness.WithAllClocksFromHarnessClockInstantiator ++
//   new chipyard.clocking.WithPassthroughClockGenerator ++
//   //new chipyard.config.WithUniformBusFrequencies(100) ++
//   new WithFPGAFrequency(100) ++
//   new WithZCU106Tweaks ++
//   new boom.v4.common.WithNMegaBooms(1) ++
//   new chipyard.config.AbstractConfig
// )

// class GigaBoom extends Config(
//   new chipyard.harness.WithAllClocksFromHarnessClockInstantiator ++
//   new chipyard.clocking.WithPassthroughClockGenerator ++
//   //new chipyard.config.WithUniformBusFrequencies(100) ++
//   new WithFPGAFrequency(100) ++
//   new WithZCU106Tweaks ++
//   new boom.v4.common.WithNGigaBooms(1) ++
//   new chipyard.config.AbstractConfig
// )

class RocketMultiDomain1GHz extends Config(
  new WithZCU106Tweaks ++
  //new freechips.rocketchip.rocket.WithAsynchronousCDCs(8, 3) ++ // Add async crossings between RocketTile and uncore
  new chipyard.config.WithControlBusFrequency(100.0) ++
  new chipyard.config.WithSystemBusFrequency(100.0) ++
  new chipyard.config.WithMemoryBusFrequency(100.0) ++    
  new chipyard.config.WithFrontBusFrequency(100.0) ++
  new chipyard.config.WithPeripheryBusFrequency(100.0) ++ 
  new chipyard.config.WithTileFrequency(500.0) ++ 
  new chipyard.config.WithFbusToSbusCrossingType(AsynchronousCrossing()) ++ // Add Async crossing between FBUS and SBUS
  new chipyard.config.WithCbusToPbusCrossingType(AsynchronousCrossing()) ++ // Add Async crossing between PBUS and CBUS
  new chipyard.config.WithSbusToMbusCrossingType(AsynchronousCrossing()) ++ // Add Async crossings between backside of L2 and MBUS
 // new freechips.rocketchip.subsystem.WithNHugeCores(1) ++
  new chipyard.config.AbstractConfig
)

class WithFPGAFrequency(fMHz: Double) extends Config(
  new chipyard.harness.WithHarnessBinderClockFreqMHz(fMHz)
  // new chipyard.config.WithSystemBusFrequency(fMHz) ++
  // new chipyard.config.WithPeripheryBusFrequency(fMHz) ++
  // new chipyard.config.WithControlBusFrequency(fMHz) ++
  // new chipyard.config.WithFrontBusFrequency(fMHz) ++
  // new chipyard.config.WithMemoryBusFrequency(fMHz)
)

class WithFPGAFreq25MHz extends WithFPGAFrequency(25)
class WithFPGAFreq50MHz extends WithFPGAFrequency(50)
class WithFPGAFreq75MHz extends WithFPGAFrequency(75)
class WithFPGAFreq100MHz extends WithFPGAFrequency(100)

class KostisZCU106Config extends Config( // kostis thesis 

  //  ---------- latest edition  ---------- //
  // new chipyard.harness.WithAllClocksFromHarnessClockInstantiator ++
  // new chipyard.clocking.WithPassthroughClockGenerator ++
  // new chipyard.config.WithUniformBusFrequencies(100) ++
  // new WithFPGAFrequency(100) ++
  // new WithZCU106Tweaks ++
  // new Data_Reuse.WithLUMAXAccelerator ++ 

  // /// ----- Rocket Cores ----- // 
  // // new freechips.rocketchip.rocket.WithNMedCores(1) ++   // Mid core 
  // // new freechips.rocketchip.rocket.WithNMedCoresFPU(1) ++ //add custom core in ~/Documents/Chipyard/latest/generators/rocket-chip/src/main/scala/rocket/Configs.scala
  // // new freechips.rocketchip.rocket.CorrectWithNMediumCoresNoBwslowdown(1,2) ++
  // // new freechips.rocketchip.subsystem.WithNMedCoresWithFPU(1) // Medium Core with FPU so can support Linux  

  // new chipyard.config.AbstractConfig
  //  ---------- latest edition  ---------- //


  //  ---------- 1.11.0 ---------- //
  new WithZCU106Tweaks ++

  // Select Config
  // new chipyard.RocketConfig 
  // new chipyard.GemminiRocketConfig
  // new chipyard.SmallNVDLARocketConfig
  // new chipyard.LargeNVDLARocketConfig
  // new chipyard.DataReuseRocketConfig

  //  ---------- 1.11.0 ---------- //

)

// class RocketZCU106Config extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.RocketConfig
// )

// class RocketDefaultZCU106Config extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.RocketConfigDefault
// )

// // class AtomCtrl9CorrectMedNoBwslowdownRocketZCU106Config extends Config(
// //   new WithZCU106Tweaks ++
// //   new chipyard.AtomCtrl9CorrectMedNoBwslowdownRocketConfig
// // )

// // class AtomCtrl9CorrectMedNoBwslowdownRocketZCU106ConfigDefault extends Config(
// //   new WithZCU106Tweaks ++
// //   new chipyard.AtomCtrl9CorrectMedNoBwslowdownRocketConfigDefault
// // )

// // class AtomCtrl20CorrectMedNoBwslowdownRocketZCU106ConfigDefault extends Config(
// //   new WithZCU106Tweaks ++
// //   new chipyard.AtomCtrl20CorrectMedNoBwslowdownRocketConfigDefault
// // )

// // class CorrectMedNoBwslowdownRocketZCU106Config extends Config(
// //   new WithZCU106Tweaks ++
// //   new chipyard.CorrectMedNoBwslowdownRocketConfig
// // )

// // class CorrectMedNoBwslowdownRocketZCU106ConfigDefault extends Config(
// //   new WithZCU106Tweaks ++
// //   new chipyard.CorrectMedNoBwslowdownRocketConfigDefault
// // )

// class AtomCtrl9LookupMoreChecksCorrectMedNoBwslowdownRocketZCU106ConfigDefault extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.AtomCtrl9LookupMoreChecksCorrectMedNoBwslowdownRocketConfigDefault
// )

// class AtomCtrl9LookupMoreChecksDualALUSavvinaCorrectMedNoBwslowdownRocketZCU106ConfigDefault extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.AtomCtrl9LookupMoreChecksDualALUSavvinaCorrectMedNoBwslowdownRocketConfigDefault
// )

// class CorrectMedNoBwslowdownRocketZCU106ConfigDefaultWithPerfCounters extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.CorrectMedNoBwslowdownRocketConfigDefaultWithPerfCounters
// )

// // class SavvinaZCU106Config extends Config(
// //   new WithZCU106Tweaks ++
// //   new chipyard.PrefetcherRocketConfig
// // )

// class ListPref4LookupMoreChecksALUDualALB32CorrectMedNoBwslowdownRocketZCU106Config extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPref4LookupMoreChecksALUDualALB32CorrectMedNoBwslowdownRocketConfig
// )

// class ListPref9LookupMoreChecksALUDualALBCorrectMedNoBwslowdownRocketZCU106Config extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPref9LookupMoreChecksALUDualALBCorrectMedNoBwslowdownRocketConfig
// )

// class ListPref4ALB32CorrectMedNoBwslowdownRocketZCU106Config extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPref4ALB32CorrectMedNoBwslowdownRocketConfig
// )

// class ListPref4ALB256CorrectMedNoBwslowdownRocketZCU106Config extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPref4ALB256CorrectMedNoBwslowdownRocketConfig
// )

// class ListPref6ALB256CorrectMedNoBwslowdownRocketZCU106Config extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPref6ALB256CorrectMedNoBwslowdownRocketConfig
// )

// class ListPref9ALB256CorrectMedNoBwslowdownRocketZCU106Config extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPref9ALB256CorrectMedNoBwslowdownRocketConfig
// )

// class ListPrefCorrect4ALB256CorrectMedNoBwslowdownRocketZCU106Config extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPrefCorrect4ALB256CorrectMedNoBwslowdownRocketConfig
// )

// class ListPrefDump4ALB256CorrectMedNoBwslowdownRocketZCU106Config extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPrefDump4ALB256CorrectMedNoBwslowdownRocketConfig
// )

// class ListPrefInitial4ALB32CorrectMedNoBwslowdownRocketZCU106Config extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPrefInitial4ALB32CorrectMedNoBwslowdownRocketConfig
// )

// class ListPrefInitial4ALB256CorrectMedNoBwslowdownRocketZCU106Config extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPrefInitial4ALB256CorrectMedNoBwslowdownRocketConfig
// )

// class ListPref5NoswaitprefALB256CorrectMedNoBwslowdownRocketZCU106Config extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPref5NoswaitprefALB256CorrectMedNoBwslowdownRocketConfig
// )

// class ListPref5NoswaitprefALB256CorrectMedNoBwslowdownRocketZCU106ConfigPerfCounts29 extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPref5NoswaitprefALB256CorrectMedNoBwslowdownRocketConfigPerfCounts29
// )

// class ListPref4NoswaitprefALB32CorrectMedNoBwslowdownRocketZCU106Config extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPref4NoswaitprefALB32CorrectMedNoBwslowdownRocketConfig
// )

// class ListPref4NoswaitprefRegNextALB256CorrectMedNoBwslowdownRocketZCU106Config extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPref4NoswaitprefRegNextALB256CorrectMedNoBwslowdownRocketConfig
// )

// class ListPref4SpersistCorrectMedNoBwslowdownRocketZCU106Config extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPref4SpersistCorrectMedNoBwslowdownRocketConfig
// )

// class ListPref4CorrectMedNoBwslowdownRocketZCU106ConfigFix1 extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPref4CorrectMedNoBwslowdownRocketConfigFix1
// )

// class ListPref4CorrectMedNoBwslowdownRocketZCU106ConfigFix2 extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPref4CorrectMedNoBwslowdownRocketConfigFix2
// )

// class ListPref9CorrectMedNoBwslowdownRocketZCU106Config extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPref9CorrectMedNoBwslowdownRocketConfig
// )

// class ListPref4CorrectMedNoBwslowdownRocketZCU106ConfigALB128 extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPref4CorrectMedNoBwslowdownRocketConfigALB128
// )

// class ListPref4CorrectMedNoBwslowdownRocketZCU106Config2048X1 extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPref4CorrectMedNoBwslowdownRocketConfig2048X1
// )

// class ListPref4CorrectMedNoBwslowdownRocketZCU106Config2048X12ndTRY extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPref4CorrectMedNoBwslowdownRocketConfig2048X12ndTRY
// )

// class ListPref4CorrectMedNoBwslowdownRocketZCU106Config2048X13rdTRY extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPref4CorrectMedNoBwslowdownRocketConfig2048X13rdTRY
// )

// class ListPref4CorrectMedNoBwslowdownRocketZCU106Config2048X14rthTRYChangeSize extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPref4CorrectMedNoBwslowdownRocketConfig2048X14rthTRYChangeSize
// )

// class ListPref4CorrectMedNoBwslowdownRocketZCU106Config2048X15thTRYFixPATATTR extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPref4CorrectMedNoBwslowdownRocketConfig2048X15thTRYFixPATATTR
// )

// class ListPref4CorrectMedNoBwslowdownRocketZCU106Config2048X11CycleRDLatency extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPref4CorrectMedNoBwslowdownRocketConfig2048X11CycleRDLatency
// )

// class ListPref4CorrectMedNoBwslowdownRocketZCU106Config2048X11CycleRDLatencyNoren extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPref4CorrectMedNoBwslowdownRocketConfig2048X11CycleRDLatencyNoren
// )

// class ListPref4CorrectMedNoBwslowdownRocketZCU106Config2048X15thTRYFixPATATTRALB128 extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPref4CorrectMedNoBwslowdownRocketConfig2048X15thTRYFixPATATTRALB128
// )

// class ListPref4CorrectMedNoBwslowdownRocketZCU106Config2048X15thTRYFixPATATTRALB2048 extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPref4CorrectMedNoBwslowdownRocketConfig2048X15thTRYFixPATATTRALB2048
// )

// class ListPref4CorrectMedNoBwslowdownRocketZCU106Config2048X15thTRYFixPATATTRMoreFixes extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.ListPref4CorrectMedNoBwslowdownRocketConfig2048X15thTRYFixPATATTRMoreFixes
// )

// // class ListPref5CorrectMedNoBwslowdownRocketZCU106Config extends Config(
// //   new WithZCU106Tweaks ++
// //   new chipyard.ListPref5CorrectMedNoBwslowdownRocketConfig
// // )

// // class ListPrefCorrectMedNoBwslowdownRocketZCU106Config extends Config(
// //   new WithZCU106Tweaks ++
// //   new chipyard.ListPrefCorrectMedNoBwslowdownRocketConfig
// // )

// // class ListPrefManyNodesCorrectMedNoBwslowdownRocketZCU106Config extends Config(
// //   new WithZCU106Tweaks ++
// //   new chipyard.ListPrefManyNodesCorrectMedNoBwslowdownRocketConfig
// // )

// // class ListPrefManyNodesCorrectMedNoBwslowdownRocketZCU106ConfigNODEBUG extends Config(
// //   new WithZCU106Tweaks ++
// //   new chipyard.ListPrefManyNodesCorrectMedNoBwslowdownRocketConfig
// // )

// // class AtomCtrl4CorrectMedNoBwslowdownRocketZCU106Config extends Config(
// //   new WithZCU106Tweaks ++
// //   new chipyard.AtomCtrl4CorrectMedNoBwslowdownRocketConfigDefault
// // )

// class AtomCtrl4CorrectMedNoBwslowdownRocketZCU106Config2048X1 extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.AtomCtrl4CorrectMedNoBwslowdownRocketConfigDefault2048X1
// )

// class AtomCtrl4CorrectMedNoBwslowdownRocketZCU106Config2048X11CycleRDLatency extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.AtomCtrl4CorrectMedNoBwslowdownRocketConfigDefault2048X11CycleRDLatency
// )

// // class AccumRoccCorrectMedNoBwslowdownRocketZCU106Config extends Config(
// //   new WithZCU106Tweaks ++
// //   new chipyard.AccumRoccCorrectMedNoBwslowdownRocketConfig
// // )

// // class ExampleRoccRocketZCU106Config extends Config(
// //   new WithZCU106Tweaks ++
// //   new chipyard.ExampleRoccRocketConfig
// // )

// // class PrefetcherRocketWithFPUZCU106Config extends Config(
// //   new WithZCU106Tweaks ++
// //   new chipyard.PrefetcherRocketConfigWithFPU
// // )

// // class AtomControllerRocketZCU106Config extends Config(
// //   new WithZCU106Tweaks ++
// //   new chipyard.AtomControllerRocketConfig
// // )

// // class StridePrefetcherRocketZCU106Config extends Config(
// //   new WithZCU106Tweaks ++
// //   new chipyard.StridePrefetcherRocketConfig
// // )

// // class IndirectPrefetcherRocketZCU106Config extends Config(
// //   new WithZCU106Tweaks ++
// //   new chipyard.IndirectPrefetcherRocketConfig
// // )

// // class IndirectPrefetcherBFSRocketZCU106Config extends Config(
// //   new WithZCU106Tweaks ++
// //   new chipyard.IndirectPrefetcherBFSRocketConfig
// // )

// // class IndirectPrefetcherStreamOnlyRocketZCU106Config extends Config(
// //   new WithZCU106Tweaks ++
// //   new chipyard.IndirectPrefetcherStreamOnlyRocketConfig
// // )

// // class ALURocketZCU106Config extends Config(
// //   new WithZCU106Tweaks ++
// //   new chipyard.ALURocketConfig
// // )

// // class BoundsCheckerRocketZCU106Config extends Config(
// //   new WithZCU106Tweaks ++
// //   new chipyard.BoundsCheckerRocketConfig
// // )

// // --------------------------- Chipyard Prefetcher Configs -----------------------------

// class StridedPrefetcherCorrectMedNoBwslowdownRocketZCU106ConfigDefault extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.StridedPrefetcherCorrectMedNoBwslowdownRocketConfigDefault
// )

// class StridedPrefetcherCorrectMedNoBwslowdownRocketZCU106ConfigDefaultWithNonblockingL1 extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.StridedPrefetcherCorrectMedNoBwslowdownRocketConfigDefaultWithNonblockingL1
// )

// class StridedPrefetcherBigRocketZCU106ConfigWithNonblockingL1 extends Config(
//   new WithZCU106Tweaks ++
//   new chipyard.StridedPrefetcherBigRocketConfigWithNonblockingL1
// )