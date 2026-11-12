package chipyard.fpga.zcu106
import org.chipsalliance.cde.config.Config
class GemminiW8zcu106 extends Config(new WithZCU106Platform ++ new gemmini.testsafe.WithTestSafeGemmini(8) ++ new freechips.rocketchip.subsystem.WithoutTLMonitors ++ new freechips.rocketchip.rocket.WithNMedCores(1) ++ new chipyard.config.AbstractConfig)

class GemminiW4zcu106 extends Config(new WithZCU106Platform ++ new gemmini.testsafe.WithTestSafeGemmini(4) ++ new freechips.rocketchip.subsystem.WithoutTLMonitors ++ new freechips.rocketchip.rocket.WithNMedCores(1) ++ new chipyard.config.AbstractConfig)

class GemminiW2zcu106 extends Config(new WithZCU106Platform ++ new gemmini.testsafe.WithTestSafeGemmini(2) ++ new freechips.rocketchip.subsystem.WithoutTLMonitors ++ new freechips.rocketchip.rocket.WithNMedCores(1) ++ new chipyard.config.AbstractConfig)
