package gemmini.fpinttests

// Place in the Chipyard project (not the Gemmini subproject).
// The JVM property is read at elaboration only; the RTL has one static width.
class FPIntRocketConfig extends org.chipsalliance.cde.config.Config(
  new gemmini.GemminiFPIntConfig(enabled = true,
    weightBits = System.getProperty("fpint.weightBits", "8").toInt) ++
  new chipyard.RocketConfig)
