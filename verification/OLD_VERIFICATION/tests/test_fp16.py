"""Independent host checks of the real Linear-sw.c FP16 reference functions."""
import ctypes
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import run_fp16 as f


class FP16ReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler=shutil.which('cc')
        if not compiler:raise unittest.SkipTest('Host C compiler required')
        cls.temp=tempfile.TemporaryDirectory(prefix='lumax-fp16-c-test-')
        cls.root=Path(cls.temp.name)
        source=(f.hw.SRC/'Linear-sw.c').read_text()
        functions='static uint32_t lumax_fixed24_to_fp32'+source.split('static uint32_t lumax_fixed24_to_fp32',1)[1].split('\n#endif',1)[0]
        cls.libraries={}
        for wb in (8,4,2):
            text=f'#include <stdint.h>\n#define W_BITS {wb}\ntypedef uint16_t input_t;typedef int8_t weight_t;\n'+functions+'''
uint32_t convert(int64_t x){return lumax_fixed24_to_fp32(x);}
void batch(const uint16_t *x,const uint8_t *w,int k,int m,uint32_t *out){
  for(int col=0;col<m;col++)out[col]=lumax_fp16_dot(x,(const weight_t*)w,k,col);
}
'''
            c=cls.root/f'w{wb}.c';lib=cls.root/f'w{wb}.so';c.write_text(text)
            subprocess.run([compiler,'-shared','-fPIC','-O2',str(c),'-o',str(lib)],check=True,capture_output=True)
            library=ctypes.CDLL(str(lib))
            library.convert.argtypes=[ctypes.c_int64];library.convert.restype=ctypes.c_uint32
            library.batch.argtypes=[ctypes.POINTER(ctypes.c_uint16),ctypes.POINTER(ctypes.c_uint8),ctypes.c_int,ctypes.c_int,ctypes.POINTER(ctypes.c_uint32)]
            cls.libraries[wb]=library

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    @staticmethod
    def pack(weights,k,m,wb):
        packed=[0]*((k*m*wb+7)//8)
        for col in range(m):
            for j in range(k):
                index=col*k+j
                packed[index*wb//8]|=(weights[j*m+col]&((1<<wb)-1))<<(index*wb%8)
        return (ctypes.c_uint8*len(packed))(*packed)

    def test_every_half_encoding_against_signed_weights(self):
        x=(ctypes.c_uint16*1)();out=(ctypes.c_uint32*8)()
        for wb,lib in self.libraries.items():
            low,high=-(1<<(wb-1)),(1<<(wb-1))-1
            weights=[low,high,0,1,-1,low+1,high-1,0]
            packed=self.pack(weights,1,8,wb)
            for raw in range(65536):
                x[0]=raw;lib.batch(x,packed,1,8,out)
                expected=f.reference([raw],weights,8,True)
                if list(out)!=expected:self.fail(f'W{wb} FP16 bits {raw:04x}: {list(out)} != {expected}')

    def test_dot_products_and_directed_cases(self):
        for wb,lib in self.libraries.items():
            for case in f.vectors(wb,True,123456789):
                x=(ctypes.c_uint16*case['k'])(*case['x'])
                packed=self.pack(case['weights'],case['k'],case['m'],wb)
                out=(ctypes.c_uint32*case['m'])()
                lib.batch(x,packed,case['k'],case['m'],out)
                self.assertEqual(list(out),case['expected'],f'W{wb} '+case['name'])

    def test_fp32_rounding_and_integer_extremes(self):
        lib=self.libraries[8];rng=random.Random(87)
        for value in [0,1,-1,1<<24,-(1<<24),(1<<55)+(1<<31)+1,(1<<55)+(1<<31)-1,(1<<63)-1,-(1<<63)]+[rng.randrange(-(1<<62),1<<62) for _ in range(10000)]:
            self.assertEqual(lib.convert(value),f.round_fp32(value),value)
        self.assertEqual(lib.convert((1<<55)+(1<<31)+1),0x4f000001)
        self.assertEqual(lib.convert((1<<55)+(1<<31)-1),0x4f000000)


if __name__=='__main__':unittest.main()
