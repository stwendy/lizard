#=========================================================================
# srl
#=========================================================================

import random

from pymtl import *
from test.core.inst_utils import *
from config.general import XLEN

#-------------------------------------------------------------------------
# gen_basic_test
#-------------------------------------------------------------------------


def gen_basic_test():
  return """
    csrr x1, mngr2proc < 0x00008000
    csrr x2, mngr2proc < 0x00000003
    nop
    nop
    nop
    nop
    nop
    nop
    nop
    nop
    srl x3, x1, x2
    nop
    nop
    nop
    nop
    nop
    nop
    nop
    nop
    csrw proc2mngr, x3 > 0x00001000
    nop
    nop
    nop
    nop
    nop
    nop
    nop
    nop
  """


# ''' LAB TASK ''''''''''''''''''''''''''''''''''''''''''''''''''''''''''
# Define additional directed and random test cases.
# '''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''

#-------------------------------------------------------------------------
# gen_dest_dep_test
#-------------------------------------------------------------------------


def gen_dest_dep_test():
  return [
      gen_rr_dest_dep_test(i, "srl", 1 + i, i,
                           Bits(XLEN, (1 + i) >> i).uint())
      for i in range(0, 6)
  ]


#-------------------------------------------------------------------------
# gen_src0_dep_test
#-------------------------------------------------------------------------


def gen_src0_dep_test():
  return [
      gen_rr_src0_dep_test(i, "srl", 7 + i, 1,
                           Bits(XLEN, (7 + i) >> 1).uint())
      for i in range(0, 6)
  ]


#-------------------------------------------------------------------------
# gen_src1_dep_test
#-------------------------------------------------------------------------


def gen_src1_dep_test():
  return [
      gen_rr_src1_dep_test(i, "srl", 1 + i, i,
                           Bits(XLEN, (1 + i) >> i).uint())
      for i in range(0, 6)
  ]


#-------------------------------------------------------------------------
# gen_srcs_dep_test
#-------------------------------------------------------------------------


def gen_srcs_dep_test():
  return [
      gen_rr_srcs_dep_test(i, "srl", 1 + i, i,
                           Bits(XLEN, (1 + i) >> i).uint())
      for i in range(0, 6)
  ]


#-------------------------------------------------------------------------
# gen_srcs_dest_test
#-------------------------------------------------------------------------


def gen_srcs_dest_test():
  return [
      gen_rr_src0_eq_dest_test("srl", 25, 1, 12),
      gen_rr_src1_eq_dest_test("srl", 0xFFFFFFE7, 4, 0x0FFFFFFE),
      gen_rr_src0_eq_src1_test("srl", 10, 0),
      gen_rr_srcs_eq_dest_test("srl", 2, 0),
  ]


#-------------------------------------------------------------------------
# gen_value_test
#-------------------------------------------------------------------------


def gen_value_test():
  return [
      gen_rr_value_test("srl", 0xff00ff00, 0xffffff0f, 0x0001FE01),
      gen_rr_value_test("srl", 0x0ff00ff0, 0x000000f0, 0x00000000),
      gen_rr_value_test("srl", 0x00ff00ff, 0xfffff00f, 0x000001fe),
      gen_rr_value_test("srl", 0xf00ff00f, 0x00000ff0, 0x00000000),
  ]


#-------------------------------------------------------------------------
# gen_random_test
#-------------------------------------------------------------------------


def gen_random_test():
  asm_code = []
  for i in xrange(100):
    src0 = Bits(XLEN, random.randint(0, 0xffffffff))
    src1 = Bits(XLEN, random.randint(0, 0xffffffff))
    temp = src0.uint() >> (src1.uint() & 0x3F)
    dest = Bits(XLEN, temp, trunc=True)
    asm_code.append(
        gen_rr_value_test("srl", src0.uint(), src1.uint(), dest.uint()))
  return asm_code
