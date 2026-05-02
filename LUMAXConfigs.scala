package chipyard 

import org.chipsalliance.cde.config.{Config}
import freechips.rocketchip.diplomacy.{AsynchronousCrossing}
import freechips.rocketchip.subsystem.{InCluster}

class LUMAXROcketConfig extends Config(
  new LUMAX_PACKAGE.WithLUMAXAccelerator ++ 
  new freechips.rocketchip.subsystem.WithoutTLMonitors ++ // //speedup RTL simulation

  // new freechips.rocketchip.subsystem.WithNBigCores(1) ++  // Big Core 
  //  new freechips.rocketchip.subsystem.WithNMedCores(1) ++   // Mid core 
  // new freechips.rocketchip.subsystem.WithNMedCoresWithFPU(1) ++
   new freechips.rocketchip.subsystem.CorrectWithNMediumCoresNoBwslowdown(1,2) ++
  //new freechips.rocketchip.subsystem.WithNSmallCores(1) ++   // Small Core 
  // new chipyard.config.WithSystemBusWidth(128) ++
  // new freechips.rocketchip.subsystem.CorrectWithNMediumCoresNoBwslowdown(1,2) ++
  new chipyard.config.AbstractConfig)