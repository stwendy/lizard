#=========================================================================
# Decode FL Model
#=========================================================================

from pymtl import *
from msg import MemMsg4B
from msg.fetch import FetchPacket
from msg.decode import *
from pclib.ifcs import InValRdyBundle, OutValRdyBundle
from pclib.fl import InValRdyQueueAdapterFL, OutValRdyQueueAdapterFL
from config.general import XLEN, ILEN, ILEN_BYTES, RESET_VECTOR


class DispatchFL(Model):
    def __init__(s):
        # interfaces
        s.instr = InValRdyBundle(FetchPacket())
        s.decoded = OutValRdyBundle(DecodePacket())

        # Adapters
        s.instr_q = InValRdyQueueAdapterFL(s.instr)
        s.decoded_q = OutValRdyQueueAdapterFL(s.decoded)
        #output

        s.decode_q = OutValRdyQueueAdapterFL(DecodePacket())

        @s.tick_fl
        def tick():
            inst = s.instr_q.popleft().instr
            # Decode it and create packet
            opmap = {
                int(Opcode.OP_IMM): s.dec_op_imm,
                int(Opcode.OP): s.dec_op,
            }
            try:
                opcode = inst[RVInstMask.OPCODE]
                result = opmap[opcode.uint()](inst)
            except KeyError:
                # Invalid instruction
                raise Exception

            result.compressed = 0
            s.decoded_q.append(result)

    def dec_op_imm(s, inst):
        res = DecodePacket()
        print(res.rs1)
        res.rs1 = inst[RVInstMask.RS1]
        print(res.rs1)
        res.rs2 = 0
        res.rd = inst[RVInstMask.RD]
        shamts = {
            0b001: {0b0000000, RV64Inst.SLLI},
            0b101: {0b0000000, RV64Inst.SRLI},
            0b101: {0b0100000, RV64Inst.SRAI},
        }

        nshamts = {
            0b000: RV64Inst.ADDI,
            0b010: RV64Inst.SLTI,
            0b011: RV64Inst.SLTIU,
            0b100: RV64Inst.XORI,
            0b110: RV64Inst.ORI,
            0b111: RV64Inst.ANDI,
        }
        func3 = inst[RVInstMask.FUNCT3].uint()
        func7 = inst[RVInstMask.FUNCT7].uint()
        if (inst[RVInstMask.FUNCT3].uint() in shamts):
            res.inst = shamts[func3][func7]
            res.imm = zext(inst[RVInstMask.SHAMT], 32)
        else:
            res.inst = nshamts[func3]
            res.imm = sext(inst[RVInstMask.I_IMM], 32)

        return res

    def dec_op(s, inst):
        res = DecodePacket()
        res.rs1 = inst[RVInstMask.RS1]
        res.rs2 = inst[RVInstMask.RS2]
        res.rd = inst[RVInstMask.RD]
        res.imm = 0

        func3 = inst[RVInstMask.FUNCT3.uint()]
        func7 = inst[RVInstMask.FUNCT7.uint()]
        insts = {
            (0b000, 0b0000000): RV64Inst.ADD,
            (0b000, 0b0100000): RV64Inst.SUB,
            (0b001, 0b0000000): RV64Inst.SLL,
            (0b010, 0b0000000): RV64Inst.SLT,
            (0b011, 0b0000000): RV64Inst.SLTU,
            (0b100, 0b0000000): RV64Inst.XOR,
            (0b101, 0b0000000): RV64Inst.SRL,
            (0b101, 0b0100000): RV64Inst.SRA,
            (0b110, 0b0000000): RV64Inst.OR,
            (0b111, 0b0000000): RV64Inst.AND,
        }
        res.inst = insts[(func3, func7)]

        return res

    def line_trace(s):
        return str(s.decoded)
