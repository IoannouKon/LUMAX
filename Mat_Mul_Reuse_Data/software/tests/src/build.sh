
riscv64-unknown-elf-gcc -fno-common -fno-builtin-printf -specs=htif_nano.specs -c Linear-sw.c
riscv64-unknown-elf-gcc -static -specs=htif_nano.specs Linear-sw.o -o Linear-sw.riscv 


# riscv64-unknown-linux-gnu-gcc -c Linear-sw.c -o executable.o
# riscv64-unknown-linux-gnu-gcc -static executable.o -o Linear-sw.riscv
