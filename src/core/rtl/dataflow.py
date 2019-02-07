from msg.data import *
from msg.codes import *

from util.rtl.interface import Interface, IncludeSome, UseInterface, connect_m
from util.rtl.method import MethodSpec
from util.rtl.types import Array, canonicalize_type
from util.rtl.mux import Mux
from util.rtl.packers import Packer
from util.rtl.snapshotting_freelist import SnapshottingFreeList
from util.rtl.registerfile import RegisterFile
from core.rtl.renametable import RenameTableInterface, RenameTable

from pclib.ifcs import InValRdyBundle, OutValRdyBundle


class PregState(BitStructDefinition):

  def __init__(s, dlen):
    s.value = BitField(dlen)
    s.ready = BitField(1)


class DataFlowManagerInterface(Interface):

  def __init__(s, dlen, naregs, npregs, nsnapshots, num_src_ports,
               num_dst_ports):
    rename_table_interface = RenameTableInterface(naregs, npregs, 0, 0,
                                                  nsnapshots)

    s.Areg = rename_table_interface.Areg
    s.Preg = rename_table_interface.Preg
    s.SnapshotId = rename_table_interface.SnapshotId

    super(DataFlowManagerInterface, s).__init__(
        [
            MethodSpec(
                'get_src',
                args={
                    'areg': s.Areg,
                },
                rets={
                    'preg': s.Preg,
                },
                call=False,
                rdy=False,
                count=num_src_ports,
            ),
            MethodSpec(
                'get_dst',
                args={
                    'areg': s.Areg,
                },
                rets={
                    'success': Bits(1),
                    'preg': s.Preg,
                },
                call=True,
                rdy=False,
                count=num_dst_ports,
            ),
            MethodSpec(
                'read_tag',
                args={
                    'tag': s.Preg,
                },
                rets={
                    'ready': Bits(1),
                    'value': Bits(dlen),
                },
                call=False,
                rdy=False,
                count=num_src_ports,
            ),
            MethodSpec(
                'write_tag',
                args={
                    'tag': s.Preg,
                    'value': Bits(dlen),
                },
                rets=None,
                call=True,
                rdy=False,
                count=num_dst_ports,
            ),
            MethodSpec(
                'commit_tag',
                args={
                    'tag': s.Preg,
                },
                rets=None,
                call=True,
                rdy=False,
                count=num_dst_ports,
            ),
            MethodSpec(
                'rollback',
                args=None,
                rets=None,
                call=True,
                rdy=False,
            ),
            MethodSpec(
                'read_csr',
                args={
                    'csr_num': Bits(CSR_SPEC_NBITS),
                },
                rets={
                    'result': Bits(dlen),
                    'success': Bits(1),
                },
                call=True,
                rdy=False,
            ),
            MethodSpec(
                'write_csr',
                args={
                    'csr_num': Bits(CSR_SPEC_NBITS),
                    'value': Bits(dlen),
                },
                rets={
                    'success': Bits(1),
                },
                call=True,
                rdy=False,
            ),
            MethodSpec(
                'snapshot',
                args=None,
                rets={
                    'id_': s.SnapshotId,
                },
                call=True,
                rdy=True,
            ),
            MethodSpec(
                'free_snapshot',
                args={
                    'id_': s.SnapshotId,
                },
                rets=None,
                call=True,
                rdy=False,
            ),
        ],
        bases=[
            IncludeSome(rename_table_interface, {'restore'}),
        ],
        ordering_chains=[
            [
                'write_tag', 'get_src', 'get_dst', 'commit_tag', 'read_tag',
                'snapshot', 'free_snapshot', 'restore', 'rollback'
            ],
        ],
    )


class DataFlowManager(Model):

  def __init__(s, dlen, naregs, npregs, nsnapshots, num_src_ports,
               num_dst_ports):
    UseInterface(
        s,
        DataFlowManagerInterface(dlen, naregs, npregs, nsnapshots,
                                 num_src_ports, num_dst_ports))

    s.PregState = PregState(dlen)
    s.mngr2proc = InValRdyBundle(Bits(dlen))
    s.proc2mngr = OutValRdyBundle(Bits(dlen))

    # used to allocate snapshot IDs
    s.snapshot_allocator = SnapshottingFreeList(nsnapshots, 1, 1, nsnapshots)

    # Reserve the highest tag for x0
    # Free list with 2 alloc ports, 1 free port, and AREG_COUNT - 1 used slots
    # initially
    s.free_regs = SnapshottingFreeList(
        npregs - 1,
        num_dst_ports,
        num_src_ports,
        nsnapshots,
        used_slots_initial=naregs - 1)
    # arch_used_pregs tracks the physical registers used by the current architectural state
    # arch_used_pregs[i] is 1 if preg i is used by the arf, and 0 otherwise
    # on reset, the ARF is backed by pregs [0, naregs - 1]
    arch_used_pregs_reset = [Bits(1, 0) for _ in range(npregs - 1)]
    for i in range(naregs):
      arch_used_pregs_reset[i] = Bits(1, 1)

    s.arch_used_pregs = RegisterFile(
        Bits(1),
        npregs - 1,
        0,  # no read ports needed, only a dump port
        num_dst_ports *
        2,  # have to write twice for every instruction that commits
        False,  # no read ports, so we don't need a write-read bypass
        True,  # use a write-dump bypass to reset after commit
        reset_values=arch_used_pregs_reset)

    # Build the initial rename table.
    # x0 -> don't care (will use 0)
    # xn -> n-1
    initial_map = [0] + [x for x in range(naregs - 1)]
    s.rename_table = RenameTable(naregs, npregs, num_src_ports, num_dst_ports,
                                 nsnapshots, True, initial_map)
    s.ZERO_TAG = s.rename_table.ZERO_TAG

    # Build the physical register file initial state
    preg_reset = [s.PregState() for _ in range(npregs)]
    # Build the inverse (preg -> areg map) initial state
    inverse_reset = [s.interface.Areg for _ in range(npregs)]

    # Only the first (naregs - 1) physical registers,
    # those that are initially mapped to x1..x(naregs-1)
    for x in range(naregs - 1):
      preg_reset[x].value = 0
      preg_reset[x].ready = 1
      # Initially p0 is x1
      inverse_reset[x] = x + 1

    # The physical register file, which stores the values
    # and ready states
    # Number of read ports is the same as number of source ports
    # 2 write ports are needed for every dst port:
    # The second set to update all the destination states during issue,
    # (get_dst), and the first set to write the computed value
    # (write_tag)
    # We give write_tag the first set since it is sequenced before get_dst
    # In practice, there should be no conflicts (or the processor is doing
    # something dumb). But, we must adhere to the interface even if the
    # user does something dumb and tries to write to a bogus tag which
    # is currently free.
    # Writes are bypassed before reads, and the dump/set is not used
    s.preg_file = RegisterFile(
        s.PregState(),
        npregs,
        num_src_ports,
        num_dst_ports * 2,
        True,
        False,
        reset_values=preg_reset)
    # The preg -> areg map, written to during get_dst, and read from
    # during commit
    # TODO: probably doesn't need write_read_bypass?
    # TODO: certainly doesn't need write_dump_bypass
    s.inverse = RegisterFile(
        s.interface.Areg,
        npregs,
        num_dst_ports,
        num_dst_ports,
        True,
        False,
        reset_values=inverse_reset)

    # The arhitectural areg -> preg mapping
    # Written and read only in commit
    # No write-read bypass
    # Yes write_dump bypass to allow restores to state after commits
    s.areg_file = RegisterFile(
        s.interface.Preg,
        naregs,
        num_dst_ports,
        num_dst_ports,
        False,
        True,
        reset_values=initial_map)

    # commit_tag
    s.is_commit_not_zero_tag = [Wire(1) for _ in range(num_dst_ports)]
    for i in range(num_dst_ports):
      # Determine if the commit is not the zero tag
      @s.combinational
      def check_commit(i=i):
        s.is_commit_not_zero_tag[i].v = (s.commit_tag_tag[i] !=
                                         s.ZERO_TAG) and s.commit_tag_call[i]

      # Read the areg associated with this tag
      s.connect(s.inverse.read_addr[i], s.commit_tag_tag[i])
      # Read the preg currently associated with this areg
      s.connect(s.areg_file.read_addr[i], s.inverse.read_data[i])
      # Free the preg currently backing this areg
      s.connect(s.free_regs.free_index[i], s.areg_file.read_data[i])
      # Only free if not the zero tag
      s.connect(s.free_regs.free_call[i], s.is_commit_not_zero_tag[i])

      # Write into the ARF the new preg
      s.connect(s.areg_file.write_addr[i], s.inverse.read_data[i])
      s.connect(s.areg_file.write_data[i], s.commit_tag_tag[i])
      # Only write if not the zero tag
      s.connect(s.areg_file.write_call[i], s.is_commit_not_zero_tag[i])

      # Mark the old preg used by the ARF as free
      s.connect(s.arch_used_pregs.write_addr[i], s.areg_file.read_data[i])
      s.connect(s.arch_used_pregs.write_data[i], 0)
      s.connect(s.arch_used_pregs.write_call[i], s.is_commit_not_zero_tag[i])
      # Mark the new preg used by the ARF as used
      s.connect(s.arch_used_pregs.write_addr[i + num_dst_ports],
                s.commit_tag_tag[i])
      s.connect(s.arch_used_pregs.write_data[i + num_dst_ports], 1)
      s.connect(s.arch_used_pregs.write_call[i + num_dst_ports],
                s.is_commit_not_zero_tag[i])

    # write_tag
    s.is_write_not_zero_tag = [Wire(1) for _ in range(num_dst_ports)]
    for i in range(num_dst_ports):
      # Determine if the write is not the zero tag
      @s.combinational
      def check_write(i=i):
        s.is_write_not_zero_tag[i].v = (s.write_tag_tag[i] !=
                                        s.ZERO_TAG) and s.write_tag_call[i]

      # All operations on the preg file are on the first set of write ports
      # at the offset 0
      # Write the value and ready into the preg file
      s.connect(s.preg_file.write_addr[i], s.write_tag_tag[i])
      s.connect(s.preg_file.write_data[i].value, s.write_tag_value[i])
      s.connect(s.preg_file.write_data[i].ready, 1)
      # Only write if not the zero tag
      s.connect(s.preg_file.write_call[i], s.is_write_not_zero_tag[i])

    # get_src
    connect_m(s.get_src, s.rename_table.lookup)

    # get_dst
    s.get_dst_need_writeback = [Wire(1) for _ in range(num_dst_ports)]
    for i in range(num_dst_ports):

      @s.combinational
      def handle_get_dst_allocate(i=i):
        if s.get_dst_areg[i] == 0:
          # zero register
          s.free_regs.alloc_call[i].v = 0
          s.get_dst_preg[i].v = s.ZERO_TAG
          s.get_dst_success[i].v = 1
          s.get_dst_need_writeback[i].v = 0
        elif s.free_regs.alloc_rdy[i]:
          # allocate a register from the freelist
          s.free_regs.alloc_call[i].v = s.get_dst_call[i]
          s.get_dst_preg[i].v = s.free_regs.alloc_index[i]
          s.get_dst_success[i].v = 1
          s.get_dst_need_writeback[i].v = s.get_dst_call[i]
        else:
          # free list is full
          s.free_regs.alloc_call[i].v = 0
          s.get_dst_preg[i].v = s.ZERO_TAG
          s.get_dst_success[i].v = 0
          s.get_dst_need_writeback[i].v = 0

      # Update the rename table
      s.connect(s.rename_table.update_areg[i], s.get_dst_areg[i])
      s.connect(s.rename_table.update_preg[i], s.get_dst_preg[i])
      s.connect(s.rename_table.update_call[i], s.get_dst_need_writeback[i])
      # All operations on the preg file are on the second set of write ports
      # at the offset +num_dst_ports
      s.connect(s.preg_file.write_addr[i + num_dst_ports], s.get_dst_preg[i])
      s.connect(s.preg_file.write_data[i + num_dst_ports].value, 0)
      s.connect(s.preg_file.write_data[i + num_dst_ports].ready, 0)
      s.connect(s.preg_file.write_call[i + num_dst_ports],
                s.get_dst_need_writeback[i])
      # save the inverse
      s.connect(s.inverse.write_addr[i], s.get_dst_preg[i])
      s.connect(s.inverse.write_data[i], s.get_dst_areg[i])
      s.connect(s.inverse.write_call[i], s.get_dst_need_writeback[i])

    # read_tag
    s.read_tag_muxes_ready = [Mux(Bits(1), 2) for _ in range(num_src_ports)]
    s.read_tag_muxes_value = [Mux(Bits(dlen), 2) for _ in range(num_src_ports)]
    s.read_tag_is_zero_tag = [Wire(1) for _ in range(num_src_ports)]
    for i in range(num_src_ports):

      @s.combinational
      def handle_read_tag(i=i):
        s.read_tag_is_zero_tag[i].v = s.read_tag_tag[i] == s.ZERO_TAG

      s.connect(s.read_tag_tag[i], s.preg_file.read_addr[i])
      s.connect(s.read_tag_muxes_ready[i].mux_in_[0],
                s.preg_file.read_data[i].ready)
      s.connect(s.read_tag_muxes_value[i].mux_in_[0],
                s.preg_file.read_data[i].value)
      s.connect(s.read_tag_muxes_ready[i].mux_in_[1], 1)
      s.connect(s.read_tag_muxes_value[i].mux_in_[1], 0)
      s.connect(s.read_tag_muxes_ready[i].mux_select, s.read_tag_is_zero_tag[i])
      s.connect(s.read_tag_muxes_value[i].mux_select, s.read_tag_is_zero_tag[i])
      s.connect(s.read_tag_ready[i], s.read_tag_muxes_ready[i].mux_out)
      s.connect(s.read_tag_value[i], s.read_tag_muxes_value[i].mux_out)

    # snapshot
    # ready if a snapshot ID is available
    s.connect(s.snapshot_rdy, s.snapshot_allocator.alloc_rdy[0])
    # snapshot ID is allocated by the allocator and returned
    s.connect(s.snapshot_id_, s.snapshot_allocator.alloc_index[0])
    s.connect(s.snapshot_call, s.snapshot_allocator.alloc_call[0])
    # snapshot the snapshot allocator into itself
    s.connect(s.snapshot_allocator.reset_alloc_tracking_target_id,
              s.snapshot_id_)
    s.connect(s.snapshot_allocator.reset_alloc_tracking_call, s.snapshot_call)
    # snapshot the freelist
    s.connect(s.free_regs.reset_alloc_tracking_target_id, s.snapshot_id_)
    s.connect(s.free_regs.reset_alloc_tracking_call, s.snapshot_call)
    # snapshot the rename table
    s.connect(s.rename_table.snapshot_target_id, s.snapshot_id_)
    s.connect(s.rename_table.snapshot_call, s.snapshot_call)

    # free_snapshot
    # just free it in the snapshot allocator, all the various
    # snapshots will eventually be overwritten
    s.connect(s.snapshot_allocator.free_index[0], s.free_snapshot_id_)
    s.connect(s.snapshot_allocator.free_call[0], s.free_snapshot_call)

    # restore
    # restore the snapshot allocator
    s.connect(s.snapshot_allocator.revert_allocs_source_id, s.restore_source_id)
    s.connect(s.snapshot_allocator.revert_allocs_call, s.restore_call)
    # restore the free list
    s.connect(s.free_regs.revert_allocs_source_id, s.restore_source_id)
    s.connect(s.free_regs.revert_allocs_call, s.restore_call)
    # restore the rename table
    s.connect(s.rename_table.restore_source_id, s.restore_source_id)
    s.connect(s.rename_table.restore_call, s.restore_call)

    # rollback
    # set the snapshot allocator to the architectural state of no snapshots
    # meaning everything is free (all ones)
    s.connect(s.snapshot_allocator.set_state, (~Bits(nsnapshots, 0)).uint())
    s.connect(s.snapshot_allocator.set_call, s.rollback_call)
    # set the free list to arch_used_pregs (note that write_dump_bypass
    # is true)
    # use a packer to collect all the bits
    s.arch_used_pregs_packer = Packer(Bits(1), npregs - 1)
    for i in range(npregs - 1):
      s.connect(s.arch_used_pregs_packer.pack_in_[i],
                s.arch_used_pregs.dump_out[i])
    # set the free regs to the complement
    @s.combinational
    def handle_rollback_free_regs_set():
      s.free_regs.set_state.v = ~s.arch_used_pregs_packer.pack_packed

    s.connect(s.free_regs.set_call, s.rollback_call)
    # set the rename table to the areg_file (again write_dump_bypass is true)
    for i in range(naregs):
      s.connect(s.rename_table.set_in_[i], s.areg_file.dump_out[i])
    s.connect(s.rename_table.set_call, s.rollback_call)

    # CSR
    @s.combinational
    def handle_read_csr():
      s.read_csr_result.v = 0
      s.read_csr_success.v = 0

#      s.mngr2proc.rdy.v = 0
#
#      if s.read_csr_call:
#        if s.read_csr_csr_num == CsrRegisters.mngr2proc:
#          s.read_csr_result.v = s.mngr2proc.msg
#          s.read_csr_success.v = s.mngr2proc.val
#          # we are ready if data is valid and we made it here
#          s.mngr2proc.rdy.v = s.mngr2proc.val
#        else:
#          # no other CSRs supported return 0
#          s.read_csr_result.v = 0
#          s.read_csr_success.v = 1

    @s.combinational
    def handle_write_csr():
      s.write_csr_success.v = 0


#      s.proc2mngr.msg.v = 0
#      s.proc2mngr.val.v = 0
#
#      if s.write_csr_call:
#        if s.write_csr_csr_num == CsrRegisters.proc2mngr:
#          s.write_csr_success.v = s.proc2mngr.rdy
#          s.proc2mngr.msg.v = s.write_csr_value
#          s.proc2mngr.val.v = s.proc2mngr.rdy
#        else:
#          # no other CSRs supported
#          s.write_csr_success.v = 1

  def line_trace(s):
    return "<dataflow>"
