from pymtl import *
from msg.datapath import *
from msg.control import *
from msg.codes import *
from util.cl.ports import InValRdyCLPort, OutValRdyCLPort
from util.cl.port_groups import OutValRdyCLPortGroup
from config.general import *
from util.line_block import LineBlock


class IssueUnitCL( Model ):

  def __init__( s, dataflow, controlflow ):
    s.decoded_q = InValRdyCLPort( DecodePacket() )

    s.execute_q = OutValRdyCLPort( IssuePacket() )
    s.muldiv_q = OutValRdyCLPort( IssuePacket() )
    s.memory_q = OutValRdyCLPort( IssuePacket() )
    s.csrr_q = OutValRdyCLPort( IssuePacket() )

    s.issued_q = OutValRdyCLPortGroup(
        [ s.execute_q, s.muldiv_q, s.memory_q, s.csrr_q ] )
    s.EXECUTE_PORT_IDX = 0
    s.MULDIV_PORT_IDX = 1
    s.MEMORY_PORT_IDX = 2
    s.CSRR_PORT_IDX = 3

    s.dataflow = dataflow
    s.controlflow = controlflow

  # Given packet p pick the index of the pipe to send it down
  def choose_pipe( s, p ):
    # TODO: There has to be a better way to do this. I think decode should set the pipe
    muldivs = [
        RV64Inst.DIV, RV64Inst.DIVU, RV64Inst.DIVUW, RV64Inst.DIVW,
        RV64Inst.MUL, RV64Inst.MULH, RV64Inst.MULHSU, RV64Inst.MULHU,
        RV64Inst.MULW, RV64Inst.REM, RV64Inst.REMU, RV64Inst.REMUW,
        RV64Inst.REMW
    ]
    if p.instr_d in muldivs:
      idx = s.MULDIV_PORT_IDX
    elif p.opcode == Opcode.LOAD or p.opcode == Opcode.STORE or p.opcode == Opcode.MISC_MEM:
      idx = s.MEMORY_PORT_IDX
    elif p.opcode == Opcode.SYSTEM:  # TODO, only csrs
      idx = s.CSRR_PORT_IDX
    else:
      idx = s.EXECUTE_PORT_IDX
    return idx

  def xtick( s ):
    if s.reset:
      s.current_d = None
      return

    # Check if frontent being squashed
    redirected = s.controlflow.check_redirect()
    if redirected.valid and not s.decoded_q.empty(
    ):  # Squash any waiting fetch packet
      s.decoded_q.deq()
      return

    if s.current_d is None:
      if s.decoded_q.empty() or not s.controlflow.register_rdy():
        return

      # Dequeue, allocate regs, and put in IQ
      s.current_d = s.decoded_q.deq()

      # Register it
      req = RegisterInstrRequest()
      resp = s.controlflow.register( req )

      s.work = IssuePacket()
      copy_decode_issue( s.current_d, s.work )
      s.work.tag = resp.tag

      s.current_rs1 = None
      s.current_rs2 = None
      s.marked_speculative = False

    pipe_idx = s.choose_pipe( s.current_d )

    # verify instruction still alive
    # TODO instead, the instructions after a branch should all be squashed in the IQ
    # and marked done (but invalid) in the ROB to free the pregs
    creq = TagValidRequest()
    creq.tag = s.work.tag
    cresp = s.controlflow.tag_valid( creq )
    if not cresp.valid:
      s.work.status = PacketStatus.SQUASHED

    if s.work.status != PacketStatus.ALIVE:
      if not s.issued_q.get( pipe_idx ).full():
        s.issued_q.enq( s.work, pipe_idx )
        s.current_d = None
      return

    if s.current_d.rs1_valid and s.current_rs1 is None:
      src = s.dataflow.get_src( s.current_d.rs1 )
      s.current_rs1 = src.tag

    if s.current_rs1 is not None and not s.work.rs1_value_valid:
      read = s.dataflow.read_tag( s.current_rs1 )
      s.work.rs1_value = read.value
      s.work.rs1_value_valid = read.ready

    if s.current_d.rs2_valid and s.current_rs2 is None:
      src = s.dataflow.get_src( s.current_d.rs2 )
      s.current_rs2 = src.tag

    if s.current_rs2 is not None and not s.work.rs2_value_valid:
      read = s.dataflow.read_tag( s.current_rs2 )
      s.work.rs2_value = read.value
      s.work.rs2_value_valid = read.ready

    # Must get sources before renaming destination!
    # Otherwise consider ADDI x1, x1, 1
    # If you rename the destination first, the instruction is waiting for itself
    if s.current_d.rd_valid and not s.work.rd_valid:
      dst = s.dataflow.get_dst( s.current_d.rd )
      s.work.rd_valid = dst.success
      s.work.rd = dst.tag

    creq = IsHeadRequest()
    creq.tag = s.work.tag
    is_head = s.controlflow.is_head( creq ).is_head

    # Done if all fields are as they should be
    # and we are at the head if we have to be
    if ( s.current_d.rd_valid == s.work.rd_valid and
         s.current_d.rs1_valid == s.work.rs1_value_valid and
         s.current_d.rs2_valid == s.work.rs2_value_valid and
         ( is_head if s.current_d.unique else True ) ):
      # if the instruction has potential to redirect early (before commit)
      # must declare instruction to controlflow
      # (essentialy creates a rename table snapshot)
      # note this happens after everything else is set -- this instruction
      # must be part of the snapshot
      if not s.marked_speculative and s.current_d.is_control_flow:
        if not s.controlflow.mark_speculative_rdy():
          return
        creq = MarkSpeculativeRequest()
        creq.tag = s.work.tag
        creq.pc_next = s.current_d.pc_next
        cresp = s.controlflow.mark_speculative( creq )
        s.marked_speculative = True

      if not s.issued_q.get( pipe_idx ).full():
        s.issued_q.enq( s.work, pipe_idx )
        s.current_d = None

      # TODO: Our tests need to hit this case!
      # assert not (s.execute_q.full() and s.memory_q.full())

  def line_trace( s ):
    return LineBlock([
        "{}".format( s.issued_q.msg().tag ),
        "{: <8} rd({}): {}".format(
            RV64Inst.name( s.issued_q.msg().instr_d ),
            s.issued_q.msg().rd_valid,
            s.issued_q.msg().rd ),
        "imm: {}".format( s.issued_q.msg().imm ),
        "rs1({}): {}".format( s.issued_q.msg().rs1_value_valid,
                              s.issued_q.msg().rs1_value ),
        "rs2({}): {}".format( s.issued_q.msg().rs2_value_valid,
                              s.issued_q.msg().rs2_value ),
    ] ).validate( s.issued_q.val() )
