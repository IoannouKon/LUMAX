# 🚀 LUMAX Accelerator Integration, Simulation & Deployment in Chipyard (v1.11.0)

This guide explains how to **integrate**, **simulate**, and **deploy** the **LUMAX Accelerator** using **Chipyard v1.11.0**.

It covers both **Verilator bare-metal testing** and **FPGA (ZCU106) Linux execution**.

You only need to copy specific folders and configuration files — **no need to upload the full Chipyard repository**.

---

## 🧩 Prerequisites

- **Chipyard v1.11.0 (stable branch)** — cloned and compiled
- Access to **ZCU106 FPGA board** (for hardware testing)

---

##  (A) Integrating LUMAX into Chipyard

### **Step 1 — Add the LUMAX Generator**

Navigate to the Chipyard generators directory:

```bash
cd chipyard/generators
```

Copy and paste the provided folder:

```
Mat_Mul_Reuse_Data
```

This folder contains:

- All **RTL Chisel sources** for the LUMAX generator
- **C test files** for accelerator validation

---

### **Step 2 — Update the Build Configuration**

Go to the root of your Chipyard repository:

```bash
cd chipyard
```

Copy and replace the existing `build.sbt` file with the provided version.

This ensures that **SBT recognizes and builds** the LUMAX accelerator.

---

### **Step 3 — Add FPGA Support (ZCU106)**

In the Chipyard root directory:

```bash
cd chipyard
```

Copy and paste the provided `fpga` folder.

This folder includes:

- **ZCU106 board support files**
- FPGA build configurations for running the accelerator

---

### **Step 4 — Add Custom RISC-V Configurations**

Navigate to the Rocket Chip subsystem configuration path:

```bash
cd chipyard/generators/rocket-chip/src/main/scala/subsystem/
```

Copy and paste the provided file:

```
Configs.scala
```

This adds:

- Custom **RISC-V configuration classes**
- Integration hooks to connect **LUMAX** with the Rocket subsystem

---

### **Step 5 — Connect LUMAX to Chipyard System**

Navigate to:

```bash
cd chipyard/generators/chipyard/src/main/scala/config/
```

Copy and paste the provided file:

```
RocketDataReuseConfigs.scala
```

This file:

- Defines the **LUMAX configuration class**
- Connects the accelerator to the **Chipyard environment**
- Creates a **custom SoC configuration** integrating Rocket cores and LUMAX

---

---

##  (B) Testing LUMAX with Verilator (Bare-Metal)

After integration, you can test the accelerator using Verilator simulation.

---

### **Step 1 — Source the Environment**

From the Chipyard root:

```bash
cd chipyard
source env.sh
```

---

### **Step 2 — Build the Verilator Simulator**

Navigate to the simulator directory:

```bash
cd sims/verilator
```

Build without waveform debugging:

```bash
#No waveform debug - Faster 
make CONFIG=DataReuseRocketConfig

#Enable Wavefroms Debug - Slower (first build binary)
make CONFIG=DataReuseRocketConfig run-binary-debug BINARY=Chipyard/generators/Mat_Mul_Reuse_Data/software/tests/src/Linear-sw.riscv

```

> ⚙️ Note: The build process may take several minutes.
> 

---

### **Step 3 — Configure and Build the Test Program**

Navigate to the LUMAX software test source:

```bash
cd chipyard/generators/Mat_Mul_Reuse_Data/software/tests/src
```

Edit `Linear-sw.c` to:

- Set **matrix dimensions**
- Adjust **activation and weight bitwidths**
- Match **hardware parameters** in `Linear-sw.c` with your LUMAX config in `Mat_Mul_Reuse_Data/src/scala/Configs.scala`

Then compile:

```bash
riscv64-unknown-elf-gcc -fno-common -fno-builtin-printf -specs=htif_nano.specs -c Linear-sw.c
riscv64-unknown-elf-gcc -static -specs=htif_nano.specs Linear-sw.o -o Linear-sw.riscv
```

You now have a **bare-metal test binary**:

```
Linear-sw.riscv
```

---

### **Step 4 — Run the Simulation**

Run the binary with the Verilator simulator:

```bash
cd chipyard/sims/verilator
./simulator-chipyard.harness-DataReuseRocketConfig  /Chipyard/generators/Mat_Mul_Reuse_Data/software/tests/src/Linear-sw.riscv
```

---

### **Step 5 — View Waveforms (Optional)**

If you built with debug support, view the waveform:

```bash
gtkwave chipyard/sims/verilator/output/chipyard.harness.TestHarness.DataReuseRocketConfig/Linear-sw.vcd
```

Use GTKWave to observe signal activity and debug your accelerator integration.

---

Or you can just use the script 

```bash
 cd Chipyard/generators/Mat_Mul_Data_Reuse/software/tests/src
 ./run_param_test.sh <RIN_MAX> <CIN_MAX> <COUT_MAX> [debug]
```

##  (C) Running LUMAX on ZCU106 with Linux

You can also deploy the LUMAX accelerator on **ZCU106 FPGA** under a Linux environment.

---

### **Step 1 — Generate FPGA Bitstream**

From the FPGA directory:

```bash
cd fpga
make SUB_PROJECT=kosszcu106 bitstream
```

This builds the FPGA bitstream for the LUMAX + RISC-V SoC design targeting ZCU106.

---

### **Step 2 — Build Linux Executable for LUMAX**

Navigate to the test software directory:

```bash
cd chipyard/generators/Mat_Mul_Reuse_Data/software/tests/src
```

Compile the Linux version of the test binary:

```bash
riscv64-unknown-linux-gnu-gcc -c Linear-sw.c -o executable.o
riscv64-unknown-linux-gnu-gcc -static executable.o -o Linear-sw.riscv
```

This produces a **Linux-compatible binary**:

```
Linear-sw.riscv
```

---

### **Step 3 — Run on ZCU106**

1. Boot Linux on the ZCU106 FPGA with your LUMAX-enabled bitstream.
2. Copy the `Linear-sw.riscv` binary to the board.
3. Run the program directly in Linux to verify correct accelerator operation.

---
