package chipyard

import org.chipsalliance.cde.config.Config

// Chipyard 1.13 moved Rocket core configurations into the rocket package.
// Keep the medium core used by the verified DataReuseRocketConfig runs.
class LUMAXROcketConfig extends Config(
  new LUMAX_PACKAGE.WithLUMAXAccelerator ++
  new freechips.rocketchip.subsystem.WithoutTLMonitors ++
  new freechips.rocketchip.rocket.WithNMedCores(1) ++
  new chipyard.config.AbstractConfig)

// Compatibility for existing scripts and historical simulation results.
class DataReuseRocketConfig extends LUMAXROcketConfig
