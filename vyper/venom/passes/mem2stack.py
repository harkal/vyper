"""
"mem2stack" algorithm for vyper, basically takes allocas
and tries to elide them where possible
"""
from vyper.venom.analysis import DFG
from vyper.venom.basicblock import IRBasicBlock, IRVariable
from vyper.venom.function import IRFunction
from vyper.venom.passes.base_pass import IRPass

# instructions which read from memory
# could be frozen / "ProxymappingType"
MUST_FLUSH = {
    "call": [3],
    "staticcall": [2],
    "delegatecall": [2],
    "return": [0],
    "revert": [0],
    "create": [1],
    "create2": [1],
}


# instructions which write to memory
VOLATILE = {
    "calldatacopy": [0],
    "mcopy": [0],
    "codecopy": [0],
    "returndatacopy": [0],
    "extcodecopy": [1],
    "call": [5],
    "staticcall": [4],
    "delegatecall": [4],
}


class Mem2Stack(IRPass):
    def _process_basic_block(self, bb: IRBasicBlock) -> None:
        self.ctx.append_basic_block(bb)

        instructions = bb.instructions.copy()
        bb.instructions.clear()

        for inst in instructions:
            if inst.opcode == "mload":
                ptr = inst.operands[0]
                origin = self.dfg.get_producing_instruction(ptr)  # type: ignore
                if origin is not None and origin.opcode == "alloca":
                    assert isinstance(inst.output, IRVariable)  # help mypy
                    if ptr not in self.allocas:
                        # we haven't seen this location yet; it's ok, we
                        # allocate a virtual register, run the mload and then
                        # put the result of that into the virtual register
                        bb.instructions.append(inst)
                        self.allocas[ptr] = bb.append_instruction("store", inst.output)
                    else:
                        # we have already allocated a virtual register, so we
                        # just need to ensure the input of store points to the
                        # existing virtual register, and the output points to
                        # whatever mload was going to output to.
                        bb.append_instruction("store", self.allocas[ptr], ret=inst.output)
                    continue
            if inst.opcode == "mstore":
                val, ptr = inst.operands
                origin = self.dfg.get_producing_instruction(ptr)  # type: ignore
                if origin is not None and origin.opcode == "alloca":
                    if ptr not in self.allocas:
                        # allocate a virtual register for this memory location,
                        # then take the value and assign it into the virtual
                        # register for this alloca (instead of actually running
                        # the mstore).
                        # note this overwrites the virtual register. this will
                        # be fixed up in make_ssa.
                        self.allocas[ptr] = bb.append_instruction("store", val)
                    else:
                        # ditto, but we have already allocated a virtual register,
                        # so we just need to ensure the output of store points to
                        # the existing virtual register
                        bb.append_instruction("store", val, ret=self.allocas[ptr])
                    continue

            if inst.opcode in MUST_FLUSH:
                for ix in MUST_FLUSH[inst.opcode]:
                    # IRInstruction operands are reversed from what you expect
                    rix = -ix - 1
                    ptr = inst.operands[rix]
                    self._flush_r(ptr, inst, bb)

            bb.instructions.append(inst)

            if inst.opcode in VOLATILE:
                # flush to memory
                for ix in VOLATILE[inst.opcode]:
                    # IRInstruction operands are reversed from what you expect
                    rix = -ix - 1
                    ptr = inst.operands[rix]
                    self._volatile_r(ptr)

    def _flush_r(self, ptr, inst, bb):
        # flush to memory
        if ptr in self.allocas:
            val = self.allocas[ptr]
            bb.append_instruction("mstore", val, ptr)
        else:
            # recurse
            op = self.dfg.get_producing_instruction(ptr)
            if op is not None:
                for item in op.get_inputs():
                    self._flush_r(item, op, bb)

    def _volatile_r(self, ptr, inst, bb):
        # invalidate cache
        if ptr in self.allocas:
            del self.allocas[ptr]
        else:
            # recurse
            op = self.dfg.get_producing_instruction(ptr)
            if op is not None:
                for item in op.get_inputs():
                    self._volatile_r(item, op, bb)

    def _run_pass(self, ctx: IRFunction) -> None:
        self.ctx = ctx
        self.dfg = DFG.build_dfg(ctx)
        self.allocas: dict = {}

        basic_blocks = ctx.basic_blocks
        ctx.basic_blocks = []

        for bb in basic_blocks:
            self._process_basic_block(bb)
