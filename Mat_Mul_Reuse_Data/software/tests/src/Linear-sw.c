//SW TESTING tempalte 
#include <stdint.h>
#include <stdlib.h> 
#include "compiler.h"
#include <stdbool.h>
#include "rocc.h"
#include <stdio.h>
#include <time.h>
#include <string.h> //memcopy


//////////////////////// TEST PARAMETERS ////////////////////////////////////

//BITWITH of elemetns 
#define IN_BITS  16
#define W_BITS   8

// 16 - 8 32 x 32 pass 
// 16 - 4 32 x 32 fail 

#define Debug 0       // print input and output matrices 
#define PC_COUNTERS  1   // print Perforamce Counters of every stage of Accelerator 
#define FPU  0           // Rocekt Core it used support FPU or not 
#define BAREMETAl_NEW 0  // BAREMETAL->0 or Linux->1 

#if BAREMETAl_NEW == 1 
    #include <stdio.h>
    #include <stdlib.h>
    #include <fcntl.h>
    #include <unistd.h>
    #include <sys/ioctl.h>
    #include <sys/mman.h>
    #include <stdint.h>
    #include <string.h>

    // IOCTL command (ίδιο με τον driver)
    #define GET_PHYS_ADDR _IOR('K', 1, uint64_t)
#endif 


//Matrix Multiplcation Parameters 
#define RIN_MAX 1
#define CIN_MAX 100
#define COUT_MAX 100
#define FILTERS_MAX 1

//debug parameters 
bool fast_mode = false; // run faster not compare if SW and HW is the same (it is just trust me)
bool ultra_fast_mode = false; // even faster skip generate random values 

#define XS 16
//HW parameters  (WARNING this must be same as CONFIGS HW)
int x_slice_max = XS;
int y_slice = 1;

int ACTIVATION_MAX_BITS = 16;
int WEIGHTS_MAX_BITS    = 8;
int mem_row_factor = 1; //Mem_row_factor

bool cahce4_8 = false; // true false 
#define SCALE    0 // 0 1 

//Matrix Dimensions 
int Rin_max  = RIN_MAX;
int Cin_max  = CIN_MAX;
int Cout_max = COUT_MAX;

#define INPUT_SIZE (RIN_MAX * CIN_MAX)
#define WEIGHT_SIZE (FILTERS_MAX * CIN_MAX * COUT_MAX)

// Calculate max input size based on IN_BITS
#if IN_BITS == 16 || IN_BITS == 8
    #define MAX_INPUT_SIZE (RIN_MAX * CIN_MAX)
#elif IN_BITS == 4
    #define MAX_INPUT_SIZE ((RIN_MAX * CIN_MAX) / 2)
#elif IN_BITS == 2
    #define MAX_INPUT_SIZE ((RIN_MAX * CIN_MAX) / 4)
#endif

// Calculate max weight size based on W_BITS
#if W_BITS == 8
    #define MAX_WEIGHT_SIZE (CIN_MAX * COUT_MAX)
#elif W_BITS == 4
    #define MAX_WEIGHT_SIZE ((CIN_MAX * COUT_MAX) / 2)
#elif W_BITS == 2
    #define MAX_WEIGHT_SIZE ((CIN_MAX * COUT_MAX) / 4)
#endif


////////////////////////////////////////////////////////////////////////////

#if IN_BITS == 16
    typedef int16_t input_t;
    int max_in = 32767;
    int min_in = -32768;
#elif IN_BITS == 8    
    typedef int8_t input_t;
    int max_in = 127;
    int min_in = -128;
#elif IN_BITS == 4    
    typedef int8_t input_t;
    int max_in = 7;
    int min_in =  -8;    
#elif IN_BITS == 2    
    typedef int8_t input_t;
    int max_in = 1;
    int min_in = -2;    
#endif    

#if W_BITS == 8
    typedef int8_t weight_t;
    int max_w = 127; //127;
    int min_w = -128; //128; 
#elif W_BITS == 4    
    typedef int8_t weight_t;
    int max_w = 7;
    int min_w = -8; 
#elif W_BITS == 2    
    typedef int8_t weight_t;
    int max_w = 1;
    int min_w = -2; 
#endif    

#if SCALE == 1
    typedef float output_t; 
#elif SCALE == 0  
    typedef int32_t output_t;  
#endif    


typedef struct {
  input_t *q; // quantized values
  #if SCALE == 1
    float s;  // scaling factors
  #endif    
} QuantizedTensor_input;

typedef struct {
  weight_t *q; // quantized values
  #if SCALE == 1
    float s;  // scaling factors
  #endif   
} QuantizedTensor_weight;


void delay(int milli_seconds)
{
    clock_t start_time = clock();
    while (clock() < start_time + milli_seconds);
}

/////////////////////////////////////////////////// CUSTOM INSTRUCTIONS ///////////////////////////////////////////////////////////// 

static inline void load_x(void *ptr,uint64_t len)
{   
	asm volatile ("fence"); 
	ROCC_INSTRUCTION_SS(0, (uint64_t) ptr,len, 0);
}

static inline void load_w(void *ptr,int x_slice_input_reg , int x_slice_weights_reg, int dma_limit) 
{
	asm volatile ("fence");
    uint64_t combined = 0;

    combined |= ((uint64_t)((uint16_t)x_slice_input_reg)) << 0;
    combined |= ((uint64_t)((uint16_t)x_slice_weights_reg)) << 16;
    combined |= ((uint64_t)((uint16_t)dma_limit)) << 32;
	ROCC_INSTRUCTION_SS(0, (uint64_t) ptr, combined, 1);
}

static inline void load_o(void *ptr,uint64_t len) 
{  
	asm volatile ("fence");
	ROCC_INSTRUCTION_SS(0, (uint64_t) ptr, len, 2);
 } 

 static inline void set_rin(uint64_t parallel_rin, uint64_t Cin ) 
{
	asm volatile ("fence");
	ROCC_INSTRUCTION_SS(0, Cin, parallel_rin, 3);
 } 


static inline void set_loops(uint64_t loops,uint64_t Rin) 
{
	asm volatile ("fence");
	ROCC_INSTRUCTION_SS(0, loops, Rin, 5);
} 

static inline void stop_busy() 
{
	asm volatile ("fence");
	ROCC_INSTRUCTION_SS(0, 0, 0, 6);
} 

static inline void set_scale(float scale) 

{
	asm volatile ("fence");

    uint32_t scale_bits;
    memcpy(&scale_bits, &scale, sizeof(scale)); // safely convert float to raw bits

	ROCC_INSTRUCTION_SS(0, (uint64_t)scale_bits, 0, 7);
} 

static inline void set_bitwidths(int  input_bits,int weight_bits,int output_bits) 

{
	asm volatile ("fence");
    uint64_t result = 0;
    result |= ((uint64_t)(output_bits & 0x3F)) << 12;
    result |= ((uint64_t)(weight_bits & 0x3F)) << 6;
    result |= ((uint64_t)(input_bits & 0x3F));
	ROCC_INSTRUCTION_SS(0, (uint64_t)result, 0, 8);
} 

static inline unsigned long read_rd() 

{
	asm volatile ("fence");
    uint64_t result = 0;
	unsigned long value;
    ROCC_INSTRUCTION_D(0,value,9); //CUSTOM_READ_OUTPUT_FUNCT
	return value; 
}

static inline void start_calculation() 
{
	asm volatile ("fence");
	ROCC_INSTRUCTION_SS(0, 0, 0, 10);
} 


/////////////////////////////////////////////////// CUSTOM INSTRUCTIONS /////////////////////////////////////////////////////////////


typedef struct {
    int Rin;          
    int Cin;          
    int Cout;        
    int XBitWidth;    
    int WBitWidth;   
    int x_slice;      
    int w_slice;      
    int num_filters; 
    int parallel_rin; 

} DataReuseParams;

static inline long rdcycle(void)
{
	long cycle;
	asm volatile ("csrr %[cycle], cycle" : [cycle] "=r" (cycle));
	return cycle;
}

void printMatrices_in(DataReuseParams p, input_t *inputMatrix) {
    // Printing Input Matrix
    #if IN_BITS == 16 || IN_BITS == 8  
        printf("Input Matrix (%d x %d):\n", p.Rin, p.Cin);
        for (int i = 0; i < p.Rin; i++) {
            for (int j = 0; j < p.Cin; j++) {
                int index = i * p.Cin + j;
                printf("%d ", inputMatrix[index]);
            }
            printf("\n");  // Newline after each row
        }
    #elif IN_BITS == 4     
        int total_elements = p.Rin * p.Cin;
        for (int i = 0; i < total_elements / 2; i++) {
            int8_t packed = inputMatrix[i];  // Περιέχει δύο int4_t
            
            int8_t high = (packed >> 4) & 0x0F;
            int8_t low  = packed & 0x0F;

            if (high & 0x08) high |= 0xF0;  
            if (low & 0x08)  low  |= 0xF0;

            printf("%d ", low);  
            printf("%d ", high);

            if ((i * 2 + 2) % p.Cin == 0) printf("\n");
        }
    #elif IN_BITS == 2
        printf("Input Matrix (%d x %d):\n", p.Rin, p.Cin);
        int total_elements = p.Rin * p.Cin;

        for (int i = 0; i < total_elements / 4; i++) {
            int8_t packed = inputMatrix[i];  // Περιέχει 4 int2_t

            int8_t v0 = (packed >> 0) & 0x03;
            int8_t v1 = (packed >> 2) & 0x03;
            int8_t v2 = (packed >> 4) & 0x03;
            int8_t v3 = (packed >> 6) & 0x03;

         
            if (v0 & 0x02) v0 |= 0xFC;
            if (v1 & 0x02) v1 |= 0xFC;
            if (v2 & 0x02) v2 |= 0xFC;
            if (v3 & 0x02) v3 |= 0xFC;

            printf("%d ", v0);
            printf("%d ", v1);
            printf("%d ", v2);
            printf("%d ", v3);

            if (((i * 4 + 4) % p.Cin) == 0)  printf("\n");
        }    
    #endif    
    
}

void printMatrices_w(DataReuseParams p, weight_t *weightMatrix) {
    printf("\nWeight Matrix (%d x %d):\n", p.Cin, p.Cout);
    for (int f = 0; f < 1; f++) {
        printf("Filter %d\n", f);
        for (int c = 0; c < p.Cin; c++) {
            for (int w = 0; w < p.Cout; w++) {
                int logical_index = f * p.Cin * p.Cout + w * p.Cin + c;

                #if W_BITS == 8
                    int8_t val = weightMatrix[logical_index];
                    printf("%d ", val);

                #elif W_BITS == 4
                    // 2 weights per byte
                    int packed_index = logical_index / 2;
                    int8_t packed = weightMatrix[packed_index];
                    int shift = (logical_index % 2) * 4;
                    int8_t val = (packed >> shift) & 0x0F;
                    // Sign-extend 4-bit
                    if (val & 0x08) val |= 0xF0;
                    printf("%d ", val);

                #elif W_BITS == 2
                    // 4 weights per byte
                    int packed_index = logical_index / 4;
                    int8_t packed = weightMatrix[packed_index];
                    int shift = (logical_index % 4) * 2;
                    int8_t val = (packed >> shift) & 0x03;
                    // Sign-extend 2-bit
                    if (val & 0x02) val |= 0xFC;
                    printf("%d ", val);
                #endif
            }
            printf("\n");
        }
        printf("\n");
    }
}


void printMatrices_out(DataReuseParams p, output_t *outputMatrix_SW, output_t *outputMatrix_HW) {
    // Printing Output Matrix SW
    printf("\nOutput Matrix SW \n");
    for (int f = 0; f < 1; f++) {
        printf("Filter %d\n", f);
        for (int r = 0; r < p.Rin; r++) {
            for (int c = 0; c < p.Cout; c++) {
                int index = f * p.Rin * p.Cout + r * p.Cout + c; 
                #if SCALE == 1
                    printf("%.6f ", outputMatrix_SW[index]);
                #elif SCALE == 0  
                    printf("%d ", outputMatrix_SW[index]);
                #endif        

            }
            printf("\n");  // Newline after each row in Rin dimension
        }
        printf("\n");  // Newline after each filter
    }

    // Printing Output Matrix HW
    printf("\nOutput Matrix HW (num_filters x Rin x Cout):\n");
    for (int f = 0; f < 1; f++) {
        for (int r = 0; r < p.Rin; r++) {
            for (int c = 0; c < p.Cout; c++) {
                int index = f * p.Rin * p.Cout + r * p.Cout + c;
                #if SCALE == 1
                    printf("%.6f ", outputMatrix_HW[index]);
                #elif SCALE == 0  
                    printf("%d ", outputMatrix_HW[index]);
                #endif  
            }
            printf("\n");  // Newline after each row in Rin dimension
        }
        printf("\n");  // Newline after each filter
    }
}


void generate_random_matrix_in(input_t *matrix, int size) {
    // Seed the random number generator to get different values on each run
    srand(time(NULL)); 
    
    #if IN_BITS == 16 || IN_BITS == 8
        for (int i = 0; i < size; ++i) { 
            matrix[i] = i; // min_in + rand() % (max_in - min_in + 1);
        }

    #elif IN_BITS == 4
        for (int i = 0; i < size / 2; ++i) {
            int8_t packed = 0;
            for (int j = 0; j < 2; j++) {
                int8_t val = min_in + rand() % (max_in - min_in + 1);  // signed 4-bit: [-8, 7]
                val &= 0x0F;  // keep only 4 bits
                packed |= (val << (j * 4));
            }
            matrix[i] = packed;
        }

    #elif IN_BITS == 2
        for (int i = 0; i < size / 4; ++i) {
            int8_t packed = 0;
            for (int j = 0; j < 4; j++) {
                int8_t val = min_in + rand() % (max_in - min_in + 1);  // signed 2-bit: [-2, 1]
                val &= 0x03;  // keep only 2 bits
                packed |= (val << (j * 2));
            }
            matrix[i] = packed;
        }
    #endif
}


void generate_random_matrix_w(QuantizedTensor_weight *matrix, int size, int filters) {   
    srand(time(NULL));  // Seed για διαφορετικά αποτελέσματα κάθε φορά

    #if W_BITS == 8
    for (int f = 0; f < filters; f++) {
        for (int i = 0; i < size; ++i) { 
            matrix[f].q[i] = (weight_t)(min_w + rand() % (max_w - min_w + 1));
        }
    }

    #elif W_BITS == 4
    // 2 values per byte
    for (int f = 0; f < filters; f++) {
        for (int i = 0; i < size; i += 2) {
            int8_t val1 =  min_w + rand() % (max_w - min_w + 1);
            int8_t val2 =  min_w + rand() % (max_w - min_w + 1);
            val1 &= 0x0F;  // keep lower 4 bits
            val2 &= 0x0F;
            matrix[f].q[i / 2] = (val2 << 4) | val1;
        }
    }

    #elif W_BITS == 2
    // 4 values per byte
    for (int f = 0; f < filters; f++) {
        for (int i = 0; i < size; i += 4) {
            int8_t vals[4];
            for (int j = 0; j < 4; ++j) {
                vals[j] = min_w + rand() % (max_w - min_w + 1);
                vals[j] &= 0x03;  // keep lower 2 bits
            }
            matrix[f].q[i / 4] =  (vals[3] << 6) | (vals[2] << 4) | (vals[1] << 2) | vals[0];
        }
    }
    #endif
}

void generate_random_float_matrix(float *matrix, int size, float min_val, float max_val) { //new 
    srand(time(NULL)); // Seed RNG

    for (int i = 0; i < size; ++i) {
        float rand_frac = (float)rand() / RAND_MAX; // [0, 1)
        matrix[i] = min_val + rand_frac * (max_val - min_val); // [min_val, max_val)
    }
}

#include <math.h>
#include <stdio.h>

int compare_matrices(output_t* outputMatrix_HW, output_t* outputMatrix_SW, 
                    int num_filters, int Rin, int Cout
                     #if FPU == 1
                        ,float tolerance
                    #endif

                    ) 
    {
    int correct = 1;
    int total_elements = num_filters * Rin * Cout;
    int exact_matches = 0;
    int tolerant_matches = 0;
    
    #if FPU == 1
    // Error statistics
    float max_rel_error = 0.0f;
    float sum_rel_error = 0.0f;
    float max_abs_error = 0.0f;
    #endif

    
    // for (int f = 0; f < num_filters; f++) {
        for (int r = 0; r < Rin; r++) {
            for (int c = 0; c < Cout; c++) {
                int index = 0 * Rin * Cout + r * Cout + c;
                output_t hw = outputMatrix_HW[index];
                output_t sw = outputMatrix_SW[index];
                
                // Count exact matches
                if (hw == sw) {
                    exact_matches++;
                    tolerant_matches++;
                    continue;
                }
                
                #if FPU == 1
                // Calculate errors
                float abs_error = fabsf(hw - sw);
                float rel_error = abs_error / fmaxf(fabsf(sw), 1e-9f);
                
                // Update statistics
                max_abs_error = fmaxf(max_abs_error, abs_error);
                max_rel_error = fmaxf(max_rel_error, rel_error);
                sum_rel_error += rel_error;
                
                // Check against tolerance
                if (abs_error <= tolerance) {
                    tolerant_matches++;
                } else {
                    correct = 0;
                } 
                #endif

            }
        // }
    }

     if (exact_matches != total_elements) { 
        correct = 0;
    }
    
    #if FPU == 1
    // Calculate matching statistics
    float exact_match_percent = 100.0f * exact_matches / total_elements;
    float tolerant_match_percent = 100.0f * tolerant_matches / total_elements;
    float avg_rel_error = sum_rel_error / total_elements;
    #endif
    
    // Print comprehensive report
    printf("Accuracy Report:\n");
    printf("----------------\n");
    #if FPU == 1
    printf("Exact matches:    %d/%d (%.2f%%)\n", exact_matches, total_elements, exact_match_percent);
    #elif FPU == 0
    printf("Exact matches:    %d/%d \n", exact_matches, total_elements);
    #endif


    #if SCALE == 1
    printf("Tolerant matches: %d/%d (%.2f%%)\n", tolerant_matches, total_elements, tolerant_match_percent);
    printf("Max absolute error: %.8f\n", max_abs_error);
    printf("Max relative error: %.8f%%\n", max_rel_error*100);
    printf("Avg relative error: %.8f%%\n", avg_rel_error*100); 
    #endif  
    
    if (correct) {
        #if FPU == 1
        printf("PASS (All elements within tolerance %.2e)\n", tolerance);
        #elif FPU == 0
        printf("PASS (All elements)\n");
        #endif  
    } else {
        printf("FAIL (%d elements exceeded tolerance)\n", total_elements - tolerant_matches);
    }
    
    return correct;
}

void preload_hw(
    int Cin,
    int Rin,
    int Cout,
    int x_slice,
    int parallel_rin,
    input_t *inputMatrix,
    weight_t *weightMatrix,
    output_t *outputMatrix_HW
    #if SCALE == 1
    , float scale
    #endif
) {
    int input_bits = IN_BITS;
    int w_bits = W_BITS;
    int o_bits = sizeof(output_t) * 8;
    set_bitwidths(input_bits, w_bits, o_bits);
    
    int sliceFactor,x_slice_weights_reg,x_elems,dma_limit;

    if(cahce4_8 && input_bits == 4 && w_bits == 8 ){ 
        sliceFactor = 1U << (8 - 4);
    } else { 
        sliceFactor = 1U << (WEIGHTS_MAX_BITS - w_bits);
    }
    int scaledSlice = x_slice * sliceFactor;

    
    if(cahce4_8 && input_bits == 4 && w_bits == 8 ){ 
        x_slice_weights_reg = scaledSlice; 
        x_elems = scaledSlice ;
        dma_limit = (Cin + x_elems - 1) / x_elems;

    } else { 
        x_slice_weights_reg = scaledSlice * (ACTIVATION_MAX_BITS / input_bits) / (WEIGHTS_MAX_BITS / w_bits);
        // printf("x_slice_weights_reg = %d\n",x_slice_weights_reg);
        x_elems = scaledSlice * (ACTIVATION_MAX_BITS / input_bits);
        dma_limit = (Cin + x_elems - 1) / x_elems;
    }
    
    int x_slice_input_reg = scaledSlice;

    uint64_t loops2 = (Rin + parallel_rin - 1) / parallel_rin;

    set_rin(parallel_rin, Cin);
    set_loops(loops2, Rin);

    #if SCALE == 1
        set_scale(scale);
    #endif
    
    #if BAREMETAL_NEW == 0
        // Print addresses before calling load functions
        // printf("outputMatrix_HW address: %p\n", (void*)&outputMatrix_HW[0 * Cout]);
        // printf("inputMatrix address: %p\n", (void*)&inputMatrix[0 * Cin]);
        // printf("weightMatrix address: %p\n", (void*)&weightMatrix[0]);

        load_o(&outputMatrix_HW[0 * Cout], Cout);
        load_x(&inputMatrix[0 * Cin], x_slice);
        load_w(&weightMatrix[0], x_slice_input_reg, x_slice_weights_reg, dma_limit);
    #endif


}

void HW_Mat_Mul(
    int Cin,
    int Rin,
    int Cout,
    int x_slice,
    int parallel_rin,
    input_t *inputMatrix,
    weight_t *weightMatrix,
    output_t *outputMatrix_HW
    #if SCALE == 1
    , float scale
    #endif
) {
    // // Call preload setup
    // preload(Cin, Rin, Cout, x_slice, parallel_rin, inputMatrix, weightMatrix, outputMatrix_HW
    //     #if SCALE == 1
    //     , scale
    //     #endif
    // );

    // Start computation
    start_calculation();

    asm volatile ("fence rw, rw"); // Memory sync

    stop_busy(); // Stop waiting
}

int min(int a, int b) {
    if (a < b) {
        return a;
    } else {
        return b;
    }
}

void transpose_weightMatrix(weight_t *c, int Cin, int Cout) {
    #if W_BITS == 8
    weight_t temp[Cin * Cout];

    for (int i = 0; i < Cin; ++i) {
        for (int j = 0; j < Cout; ++j) {
            temp[j * Cin + i] = c[i * Cout + j];
        }
    }

    memcpy(c, temp, sizeof(weight_t) * Cin * Cout);

    #elif W_BITS == 4
    // 2 values per byte: Cin * Cout values → (Cin * Cout + 1) / 2 bytes
    uint8_t temp[(Cin * Cout + 1) / 2];
    memset(temp, 0, sizeof(temp));

    for (int i = 0; i < Cin; ++i) {
        for (int j = 0; j < Cout; ++j) {
            int in_index = i * Cout + j;
            int in_byte = in_index / 2;
            uint8_t in_val = (in_index % 2 == 0) ? (c[in_byte] & 0x0F) : ((c[in_byte] >> 4) & 0x0F);

            int out_index = j * Cin + i;
            int out_byte = out_index / 2;
            if (out_index % 2 == 0) {
                temp[out_byte] = (temp[out_byte] & 0xF0) | (in_val & 0x0F);
            } else {
                temp[out_byte] = (temp[out_byte] & 0x0F) | ((in_val & 0x0F) << 4);
            }
        }
    }

    memcpy(c, temp, sizeof(temp));

    #elif W_BITS == 2
    // 4 values per byte: Cin * Cout values → (Cin * Cout + 3) / 4 bytes
    uint8_t temp[(Cin * Cout + 3) / 4];
    memset(temp, 0, sizeof(temp));

    for (int i = 0; i < Cin; ++i) {
        for (int j = 0; j < Cout; ++j) {
            int in_index = i * Cout + j;
            int in_byte = in_index / 4;
            int in_shift = (in_index % 4) * 2;
            uint8_t in_val = (c[in_byte] >> in_shift) & 0x03;

            int out_index = j * Cin + i;
            int out_byte = out_index / 4;
            int out_shift = (out_index % 4) * 2;

            temp[out_byte] &= ~(0x03 << out_shift);          // clear
            temp[out_byte] |= (in_val & 0x03) << out_shift;  // set
        }
    }

    memcpy(c, temp, sizeof(temp));

    #endif
}





void SW_Mat_Mul(
    int num_filters,      // Number of filters (f)
    int Rin,              // Rows in input matrix (i)
    int Cout,             // Columns in output matrix (j)
    int Cin,              // Channels in input matrix (k)
    input_t *inputMatrix, // Input matrix
    weight_t *weightMatrix, // Transposed weight matrix
    int32_t *outputMatrix_SW // Output matrix
) {
    for (int f = 0; f < num_filters; f++) {
        for (int i = 0; i < Rin; i++) {
            for (int j = 0; j < Cout; j++) {
                // Initialize output to 0 for this f, i, j
                outputMatrix_SW[f * Rin * Cout + i * Cout + j] = 0;

                // Perform matrix multiplication: Multiply and accumulate
                for (int k = 0; k < Cin; k++) {
                    // Access inputMatrix and weightMatrix directly using the calculated 1D index
                    int inputIndex = i * Cin + k; // Input matrix index: (i, k)
                    int weightIndex = j * Cin + k; // Transposed weight matrix index: (j, k) since B^T

                    // Multiply and accumulate
                    outputMatrix_SW[f * Rin * Cout + i * Cout + j] += inputMatrix[inputIndex] * weightMatrix[weightIndex];
                }
            }
        }
    }
}

void matmul_sw_uniform(
    output_t* xout,                // output: size d * m
    QuantizedTensor_input* x,      // input: size n * m (quantized)
    QuantizedTensor_weight* w,     // weights: size d * n (quantized)
    int n,                        // input vector length
    int d,                        // output vector length
    int m                         // number of input vectors
) {
    int32_t sum;

    #if SCALE == 1  
        float scale = x->s * w->s;
    #endif    

    for (int vec_i = 0; vec_i < m; vec_i++) {           // For each input vector
        for (int i = 0; i < d; i++) {                    // For each output dimension
            sum = 0;
            for (int j = 0; j < n; j++) {                // For each element in input vector and weights
                int x_val, w_val;

                // ----------------------------
                // UNPACK input x->q for vec_i-th vector and j-th element
                // ----------------------------
                #if IN_BITS == 16 || IN_BITS == 8
                    // Assuming x->q is stored row-major: input vectors are contiguous
                    x_val = x->q[vec_i * n + j];
                #elif IN_BITS == 4
                    {
                        int idx = vec_i * n + j;
                        int byte_idx = idx / 2;
                        int shift = (idx % 2) * 4;
                        x_val = (x->q[byte_idx] >> shift) & 0x0F;
                        if (x_val & 0x08) x_val |= 0xFFFFFFF0;
                    }
                #elif IN_BITS == 2
                    {
                        int idx = vec_i * n + j;
                        int byte_idx = idx / 4;
                        int shift = (idx % 4) * 2;
                        x_val = (x->q[byte_idx] >> shift) & 0x03;
                        if (x_val & 0x02) x_val |= 0xFFFFFFFC;
                    }
                #endif

                // ----------------------------
                // UNPACK weight w->q[i * n + j]
                // ----------------------------
                #if W_BITS == 16 || W_BITS == 8
                    w_val = w->q[i * n + j];
                #elif W_BITS == 4
                    {
                        int idx = i * n + j;
                        int byte_idx = idx / 2;
                        int shift = (idx % 2) * 4;
                        w_val = (w->q[byte_idx] >> shift) & 0x0F;
                        if (w_val & 0x08) w_val |= 0xFFFFFFF0;
                    }
                #elif W_BITS == 2
                    {
                        int idx = i * n + j;
                        int byte_idx = idx / 4;
                        int shift = (idx % 4) * 2;
                        w_val = (w->q[byte_idx] >> shift) & 0x03;
                        if (w_val & 0x02) w_val |= 0xFFFFFFFC;
                    }
                #endif

                sum += (int32_t)x_val * (int32_t)w_val;
            }

            #if SCALE == 1  
                xout[vec_i * d + i] = (float)sum * scale;
            #else
                xout[vec_i * d + i] = sum;
            #endif  
        }
    }
}


unsigned long int_hw_cycles,float_hw_cycles,start,end;


// Main matmul with uniform row-wise scale
void matmul_hw_uniform(
    output_t* xout,                 // float output vector (size d)
    QuantizedTensor_input* x,         // quantized input vector (n,)
    QuantizedTensor_weight* w,         // quantized weight matrix (d x n)
    int n,                      // input dimension
    int d,                       // output dimension
    int x_slice,
    int y_slice,
    int vectors
) {
    #if SCALE == 1
        float scale = x->s * w->s;
        HW_Mat_Mul(n,1,d,x_slice,y_slice,x->q,w->q,xout,scale);
    #endif      
     
    start = rdcycle();
    HW_Mat_Mul(n,vectors,d,x_slice,y_slice,x->q,w->q,xout);
    end = rdcycle();
    int_hw_cycles = end - start;

}

#define FP32_STRICT_TOL  1e-6f  // Debug/verification
#define FP32_LOOSE_TOL   1e-3f  // Optimized kernels

int main() { 
    printf("HW parameters (WARNING: same as CONFIGS HW): x_slice_max = %d, y_slice_max = %d, ACTIVATION_MAX_BITS = %d, WEIGHTS_MAX_BITS = %d, cahce4_8 = %s\n", x_slice_max, y_slice, ACTIVATION_MAX_BITS, WEIGHTS_MAX_BITS, cahce4_8 ? "true" : "false");

    unsigned long sw_cycles,hw_cycles,start,end;
    
    int filters_max = 1;

    DataReuseParams p = {
        .w_slice        = 1,
        .num_filters    = 1
        }; 
    
    QuantizedTensor_input inputMatrix;
    QuantizedTensor_weight weightMatrix[filters_max]; //new
    int COUT [filters_max]; //new


#if BAREMETAl_NEW == 0
  printf("Hello Baremetal Mode\n");

    // static arrays for bare-metal
    static input_t inputMatrix_q[MAX_INPUT_SIZE];
    static weight_t weightMatrix_q[FILTERS_MAX][MAX_WEIGHT_SIZE];
    output_t outputMatrix_HW[filters_max][Rin_max * Cout_max]; 

    inputMatrix.q = inputMatrix_q;
    for (int f = 0; f < p.num_filters; f++)
        weightMatrix[f].q = weightMatrix_q[f];
#endif

#if BAREMETAl_NEW == 1

  printf("Hello Linux Mode\n");
 // Ορίζουμε το συνολικό μέγεθος του DMA buffer
    #define DMA_BUFFER_SIZE (MAX_INPUT_SIZE * sizeof(input_t) + \
                         FILTERS_MAX * MAX_WEIGHT_SIZE * sizeof(weight_t) + \
                         filters_max * Rin_max * Cout_max * sizeof(output_t))

    int accel_fd;
    void *dma_buffer;

    // Εδώ προσθέτουμε το πεδίο για τη φυσική διεύθυνση του πίνακα εξόδου
    struct dma_addrs {
        uint64_t input_phys_addr;
        uint64_t weights_phys_addr;
        uint64_t output_phys_addr; 
    };
    struct dma_addrs phys_addrs;

    // Άνοιγμα του device file του driver
    accel_fd = open("/dev/my_accel", O_RDWR);
    if (accel_fd < 0) {
        perror("Failed to open accelerator device");
        return -1;
    }

    // Χαρτογράφηση του συνολικού DMA buffer στο user-space
    size_t total_buffer_size = MAX_INPUT_SIZE * sizeof(input_t) + 
                               FILTERS_MAX * MAX_WEIGHT_SIZE * sizeof(weight_t) +
                               filters_max * Rin_max * Cout_max * sizeof(output_t);
    
    dma_buffer = mmap(NULL, total_buffer_size, PROT_READ | PROT_WRITE, MAP_SHARED, accel_fd, 0);
    if (dma_buffer == MAP_FAILED) {
        perror("mmap failed");
        close(accel_fd);
        return -1;
    }

    // Υπολογισμός των pointers για κάθε πίνακα μέσα στο DMA buffer
    inputMatrix.q = (input_t *)dma_buffer;

    for (int f = 0; f < p.num_filters; f++) {
        weightMatrix[f].q = (weight_t *)(dma_buffer + MAX_INPUT_SIZE * sizeof(input_t) + 
                                        f * MAX_WEIGHT_SIZE * sizeof(weight_t));
    }
    
    // Νέος pointer για τον πίνακα εξόδου, υπολογισμένος από το offset
    output_t (*outputMatrix_HW)[Rin_max * Cout_max] = (output_t (*)[Rin_max * Cout_max])(dma_buffer + 
                                                                                        MAX_INPUT_SIZE * sizeof(input_t) +
                                                                                        FILTERS_MAX * MAX_WEIGHT_SIZE * sizeof(weight_t));

#endif



// // maloc edition 
//    #if IN_BITS == 16
//         inputMatrix.q = (input_t*) malloc(sizeof(input_t) * input_size);
//     #elif IN_BITS == 8    
//         inputMatrix.q = (input_t*) malloc(sizeof(input_t) * input_size);
//     #elif IN_BITS == 4    
//         inputMatrix.q = (input_t*) malloc(sizeof(input_t) * input_size/2);   
//     #elif IN_BITS == 2    
//         inputMatrix.q = (input_t*) malloc(sizeof(input_t) * input_size/4);    
//     #endif    
   
//     #if W_BITS == 8
//         for(int f =0 ; f < p.num_filters ; f++){ 
//         weightMatrix[f].q = (weight_t*) malloc(sizeof(weight_t) * weight_size);
//         }
//     #elif W_BITS == 4    
//         for(int f =0 ; f < p.num_filters ; f++){ 
//         weightMatrix[f].q = (weight_t*) malloc(sizeof(weight_t) * weight_size/2);
//         }
//     #elif W_BITS == 2    
//         for(int f =0 ; f < p.num_filters ; f++){ 
//         weightMatrix[f].q = (weight_t*) malloc(sizeof(weight_t) * weight_size/4);
//         }
//     #endif    

    
    output_t outputMatrix_SW[filters_max][Rin_max * Cout_max];  
 
    if(!ultra_fast_mode){ 
        printf("Generate matrices\n"); 
        generate_random_matrix_in(inputMatrix.q,Rin_max * Cin_max);
        generate_random_matrix_w(weightMatrix,filters_max * Cin_max * Cout_max,p.num_filters);  //new 

        // extern const unsigned char _binary_precomp_input_bin_start[];
        // extern const unsigned char _binary_precomp_input_bin_end[];

        // extern const unsigned char _binary_precomp_weight_f0_bin_start[];
        // extern const unsigned char _binary_precomp_weight_f0_bin_end[];

        // /* compute sizes (in bytes) */
        // size_t input_bytes = (size_t)(_binary_precomp_input_bin_end - _binary_precomp_input_bin_start);
        // size_t weight0_bytes = (size_t)(_binary_precomp_weight_f0_bin_end - _binary_precomp_weight_f0_bin_start);

        // /* Sanity checks */
        // if (input_bytes == 0) {
        //     printf("Error: embedded input binary not found or empty. Check objcopy/link step.\n");
        // } else {
        //     /* BAREMETAL == 0: use existing writable inputMatrix_q buffer and copy into it.
        //     BAREMETAL == 1: your code maps a DMA buffer (dma_buffer) and sets inputMatrix.q = dma ptr;
        //     adjust here if dma_buffer is used instead. */
        // #if BAREMETAl_NEW == 0
        //     if (input_bytes > sizeof(inputMatrix_q)) {
        //         printf("Warning: embedded input.bin (%zu bytes) larger than inputMatrix_q buffer (%zu bytes)\n",
        //             input_bytes, (size_t)sizeof(inputMatrix_q));
        //     }
        //     memcpy(inputMatrix_q, _binary_precomp_input_bin_start, input_bytes);
        //     inputMatrix.q = inputMatrix_q;
        // #else
        //     /* Linux path: dma_buffer was mmap'd earlier and inputMatrix.q already points into dma_buffer.
        //     Copy into the dma buffer region (the offsets used in your code: inputMatrix.q = (input_t*)dma_buffer) */
        //     memcpy((void*)inputMatrix.q, _binary_precomp_input_bin_start, input_bytes);
        // #endif
        // }

        // /* Copy weights for filter 0 (expand loop for more filters) */
        // if (weight0_bytes == 0) {
        //     printf("Error: embedded weight_f0 binary not found or empty. Check objcopy/link step.\n");
        // } else {
        // #if BAREMETAl_NEW == 0
        //     if (weight0_bytes > sizeof(weightMatrix_q[0])) {
        //         printf("Warning: embedded weight_f0.bin (%zu bytes) larger than weightMatrix_q[0] (%zu bytes)\n",
        //             weight0_bytes, (size_t)sizeof(weightMatrix_q[0]));
        //     }
        //     memcpy(weightMatrix_q[0], _binary_precomp_weight_f0_bin_start, weight0_bytes);
        //     weightMatrix[0].q = weightMatrix_q[0];
        // #else
        //     /* Linux mode: copy into the mapped DMA weights region */
        //     memcpy((void*)weightMatrix[0].q, _binary_precomp_weight_f0_bin_start, weight0_bytes);
        // #endif
        // }

        // /* If you have additional filters, add memcpy for each:
        // extern const unsigned char _binary_precomp_weight_f1_bin_start[]; etc.
        // Then memcpy into weightMatrix_q[1], weightMatrix[2], ... and set weightMatrix[f].q accordingly. */

        // /* Optionally: you can skip copying and point directly to embedded read-only data:
        // inputMatrix.q = (input_t*) _binary_precomp_input_bin_start;
        // weightMatrix[0].q = (weight_t*) _binary_precomp_weight_f0_bin_start;
        // BUT this is only safe if your accelerator / DMA does not write to these buffers.
        // */

    }

    #if BAREMETAl_NEW == 1 
        // Πάρε τις φυσικές διευθύνσεις από τον driver μέσω ioctl
        #define ACCEL_GET_DMA_ADDRS  _IOR('a', 1, struct accel_phys_addrs)

        if (ioctl(accel_fd, ACCEL_GET_DMA_ADDRS, &phys_addrs) < 0) {
            perror("ioctl failed");
            munmap(dma_buffer, total_buffer_size);
            close(accel_fd);
            return -1;
        }
    #endif

    
    #if SCALE == 1
    printf("Generates Scales\n");

    float rand_frac = (float)rand() / RAND_MAX; // [0, 1)
    inputMatrix.s =  0.01f + rand_frac * (1.0f - 0.01f); // [min_val, max_val)

    rand_frac = (float)rand() / RAND_MAX; // [0, 1)
    for(int f =0 ; f < p.num_filters ; f++){ //new
        rand_frac = (float)rand() / RAND_MAX; // [0, 1)
        weightMatrix[f].s = 0.01f + rand_frac * (0.5f - 0.01f); // [min_val, max_val)
    }
    #endif 

        
    p.Rin  = Rin_max;
    p.Cin  = Cin_max; 
    p.Cout = Cout_max;
    sw_cycles = 0; 
    for(int c = 0; c < p.num_filters; c++){
        COUT[c] = Cout_max;
    }

   
    if(!ultra_fast_mode){
        for(int f =0; f < p.num_filters; f++){ //new
            transpose_weightMatrix(weightMatrix[f].q,p.Cin,COUT[f]);
        }
    }    

    if(!fast_mode){
        printf("SW Mat-Mul Start\n");
        start = rdcycle();
        for(int f =0; f < p.num_filters; f++){ //new
            matmul_sw_uniform(outputMatrix_SW[f], &inputMatrix, &weightMatrix[f], p.Cin,COUT[f],p.Rin); 
        }
        end = rdcycle();
        sw_cycles = end-start;  
    } 

    
    p.x_slice = x_slice_max*mem_row_factor;
    p.parallel_rin = y_slice;

    printf("### Rin = %d, Cin = %d, Cout = %d, X Slice = %d, Parallel Rows = %d, input_t: %d bits, weight_t: %d bits, output_t: %d bits ###\n",
        p.Rin, p.Cin, p.Cout, p.x_slice, p.parallel_rin, IN_BITS, W_BITS, sizeof(output_t) * 8);


    #if Debug == 1                             
            printMatrices_in(p, inputMatrix.q);    
        for(int f =0; f < p.num_filters; f++){
            printf("Filter %d \n",f);
            p.Cout = COUT[f];
            printMatrices_w(p, weightMatrix[f].q);    

        }
    #endif    
        


    printf("HW Mat-Mul Start\n");
    start = rdcycle();
    for(int f = 0; f < p.num_filters; f++){  

        preload_hw(p.Cin, p.Rin, COUT[f], p.x_slice, p.parallel_rin,inputMatrix.q,weightMatrix[f].q,outputMatrix_HW[f]);

        matmul_hw_uniform(outputMatrix_HW[f],&inputMatrix,&weightMatrix[f],p.Cin,COUT[f],p.x_slice, p.parallel_rin,Rin_max);
    }
    end = rdcycle();
    hw_cycles = end-start; 
    
    if(!fast_mode){
        #if FPU == 1
        double speedup = (double)sw_cycles / hw_cycles;
        #endif    

        printf("SW Mat-Mul %lu cycles, ", sw_cycles); 
        printf("HW Mat-Mul %lu cycles", hw_cycles); 
        #if FPU == 1
        printf("Speed-up: %.2f \n", speedup);
        #endif    

    }

    if(fast_mode){
        printf("HW Mat-Mul %lu cycles\n", hw_cycles); 
    }

        
    #if Debug == 1   
        for (int f =0; f < p.num_filters; f++) { 
            printf("Filter %d \n",f);
            p.Cout = COUT[f];
            printMatrices_out(p, outputMatrix_SW[f], outputMatrix_HW[f]);
        }
    #endif    

    #if FPU == 0
        if(!fast_mode){
            for(int f =0; f < p.num_filters; f++){ //new
                printf("Filter %d \n",f);
                int result = compare_matrices(outputMatrix_HW[f], outputMatrix_SW[f],1, p.Rin, COUT[f]);
                printf("\n",f);
            }
        } 
    #elif FPU == 1
        if(!fast_mode){
            for(int f =0; f < p.num_filters; f++){ //new
                printf("Filter %d \n",f);
                int result = compare_matrices(outputMatrix_HW[f], outputMatrix_SW[f],1, p.Rin, COUT[f],0.01f);
                printf("\n",f);
            }
        } 
    #endif    


    #if PC_COUNTERS == 1 && FPU == 1
        unsigned long load_x, Generate_Brams, load_w, Select_and_Accumulate, store_o,load_w_and_select;

        load_x = read_rd();
        Generate_Brams = read_rd();
        load_w = read_rd();
        Select_and_Accumulate = read_rd();
        store_o  = read_rd ();
        load_w_and_select = read_rd();

        // Calculate total
        unsigned long total_cycles = load_x + Generate_Brams +  load_w_and_select  + store_o;

        // Print actual cycles
        printf("Stage                  | Cycles     | Percentage\n");
        printf("-----------------------|------------|-----------\n");
        printf("Load X                 | %10lu | %6.2f%%\n", load_x, 100.0 * load_x / total_cycles);
        printf("Generate BRAMs         | %10lu | %6.2f%%\n", Generate_Brams, 100.0 * Generate_Brams / total_cycles);
        // printf("Load W                 | %10lu | %6.2f%%\n", load_w, 100.0 * load_w / total_cycles);
        // printf("Select & Accumulate    | %10lu | %6.2f%%\n", Select_and_Accumulate, 100.0 * Select_and_Accumulate / total_cycles);
        printf("Load W and Select      | %10lu | %6.2f%%\n", load_w_and_select, 100.0 * load_w_and_select / total_cycles);
        printf("Store O                | %10lu | %6.2f%%\n", store_o, 100.0 * store_o / total_cycles);
        printf("-----------------------|------------|-----------\n");

        printf("Load W  Alone          | %10lu | %6.2f%%\n", load_w, 100.0 * load_w / total_cycles);
        printf("Select  Alone          | %10lu | %6.2f%%\n", Select_and_Accumulate, 100.0 * Select_and_Accumulate / total_cycles);

        // Print total
        printf("-----------------------|------------|-----------\n");
        printf("Total                  | %10lu | 100.00%%\n", total_cycles);

    #elif PC_COUNTERS == 1 && FPU == 0

        unsigned long load_x, Generate_Brams, load_w, Select_and_Accumulate, store_o, load_w_and_select;

        load_x = read_rd();
        Generate_Brams = read_rd();
        load_w = read_rd();
        Select_and_Accumulate = read_rd();
        store_o = read_rd();
        load_w_and_select = read_rd();

        // Calculate total
        unsigned long total_cycles = load_x + Generate_Brams + load_w_and_select + store_o;

        // Print raw cycle counts (no percentages)
        printf("Stage                  | Cycles\n");
        printf("-----------------------|------------\n");
        printf("Load X                 | %10lu\n", load_x);
        printf("Generate BRAMs         | %10lu\n", Generate_Brams);
        printf("Load W and Select      | %10lu\n", load_w_and_select);
        printf("Store O                | %10lu\n", store_o);
        printf("-----------------------|------------\n");
        printf("Load W  Alone          | %10lu\n", load_w);
        printf("Select  Alone          | %10lu\n", Select_and_Accumulate);
        printf("-----------------------|------------\n");
        printf("Total                  | %10lu\n", total_cycles);

    #endif


    #if BAREMETAl_NEW == 1
    // Τώρα οι φυσικές διευθύνσεις βρίσκονται στην phys_addrs
    printf("Input physical address: 0x%llx\n", (unsigned long long)phys_addrs.input_phys_addr);
    printf("Weights physical address: 0x%llx\n", (unsigned long long)phys_addrs.weights_phys_addr);
    printf("Output physical address: 0x%llx\n", (unsigned long long)phys_addrs.output_phys_addr); 
    
    // ... εδώ θα έστελνες τις φυσικές διευθύνσεις στον accelerator μέσω της RoCC
    // και θα τον εκκινούσες.

    // Αποδέσμευση της μνήμης
    munmap(dma_buffer, total_buffer_size);
    close(accel_fd);
    #endif




  return 0;
}