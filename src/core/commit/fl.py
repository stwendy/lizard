from pymtl import *
from msg.decode import *
from msg.issue import *
from msg.functional import *
from msg.result import *
from msg.control import *
from pclib.ifcs import InValRdyBundle, OutValRdyBundle
from util.cl.adapters import UnbufferedInValRdyQueueAdapter, UnbufferedOutValRdyQueueAdapter
from util.line_block import LineBlock
from copy import deepcopy


class CommitFL( Model ):

  def __init__( s, dataflow, controlflow ):
    s.result_in = InValRdyBundle( ResultPacket() )
    s.result_in_q = UnbufferedInValRdyQueueAdapter( s.result_in )

    s.dataflow = dataflow
    s.controlflow = controlflow

    s.committed = Wire( INST_TAG_LEN )
    s.valid = Wire( 1 )

  def xtick( s ):
    s.result_in_q.xtick()

    if s.reset:
      s.valid.next = 0
      return

    if s.valid:
      s.valid.next = 0

    if s.result_in_q.empty():
      s.result_in_q.assert_rdy()
      return

    p = s.result_in_q.deq()

    # verify instruction still alive
    creq = TagValidRequest()
    creq.tag = p.tag
    cresp = s.controlflow.tag_valid( creq )
    if not cresp.valid:
      # if we allocated a destination register for this instruction,
      # we must free it
      if p.rd_valid:
        s.dataflow.free_tag( p.rd )
      # retire instruction from controlflow
      creq = RetireRequest()
      creq.tag = p.tag
      s.controlflow.retire( creq )
      return

    if p.rd_valid:
      s.dataflow.commit_tag( p.rd )

    # retire instruction from controlflow
    creq = RetireRequest()
    creq.tag = p.tag
    s.controlflow.retire( creq )
    s.committed.next = p.tag
    s.valid.next = 1

  def line_trace( s ):
    return LineBlock([
        "{}".format( s.committed ),
    ] ).validate( s.valid )
