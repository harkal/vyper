"""
"mem2stack" algorithm for vyper, basically takes allocas
and tries to elide them where possible
"""
from vyper.venom.analysis import DFG
from vyper.venom.basicblock import IRBasicBlock, IRVariable
from vyper.venom.function import IRFunction
from vyper.venom.passes.base_pass import IRPass

# could be frozen / "ProxymappingType"
PINNING_INSTRUCTIONS = {
    "call": 3,
    "staticcall": 2,
    "delegatecall": 2,
    "return": 0,
    "revert": 0,
    "create": 1,
    "create2": 1,
    "iload": 0,
}

POINTER = dict(mstore=0, mload=0, **PINNING_INSTRUCTIONS)


class Mem2Stack(IRPass):
    def _process_basic_block(self, bb: IRBasicBlock) -> None:
        self.ctx.append_basic_block(bb)

        instructions = bb.instructions.copy()
        bb.instructions.clear()

        for inst in instructions:
            if inst.opcode == "mload" and not self.pins[inst]:
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
            elif inst.opcode == "mstore" and not self.pins[inst]:
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

            bb.instructions.append(inst)

    # find allocas which are pinned - they are used by a
    # RETURN, CALL or REVERT instruction, so we should not elide
    # loads/stores. (can maybe do better, like only materialize the last
    # store before a pinning instruction).
    def _find_pins(self, ctx):
        self.pins = {}
        for bb in ctx.basic_blocks:
            for inst in bb.instructions:
                self._find_pins_r(inst)

    def _find_pins_r(self, inst, poison=False):
        poison = poison or inst.opcode in PINNING_INSTRUCTIONS

        if inst in self.pins and poison == self.pins[inst]:
            # already poisoned
            return poison

        print("ENTER", inst)

        # poison all uses of this alloca
        outputs = inst.get_outputs()
        for op in outputs:
            targets = self.dfg.get_uses(op)
            pre = poison
            for target in targets:
                print("POISON DOWN", poison, target)
                poison |= self._find_pins_r(target, poison=poison)
            if poison != pre:
                # we found a descendant who is poisoned; spread the
                # poison to all descendants
                print("REDO", inst)
                for target in targets:
                    print("POISON DOWN", poison, target)
                    self._find_pins_r(target, poison=poison)

        self.pins[inst] = poison

        if inst.opcode in POINTER:
            # IRInstruction operands are reversed from what you expect
            ix = -POINTER[inst.opcode] - 1
            ptr = inst.operands[ix]
            if isinstance(ptr, IRVariable):
                target = self.dfg.get_producing_instruction(ptr)
                print("POISON UP", poison, target)
                self._find_pins_r(target, poison=poison)

        return poison

    def _collect_allocas(self, ctx):
        self.allocas = {}

    def _run_pass(self, ctx: IRFunction) -> None:
        self.ctx = ctx
        self.dfg = DFG.build_dfg(ctx)

        self._find_pins(ctx)
        self._collect_allocas(ctx)

        basic_blocks = ctx.basic_blocks
        ctx.basic_blocks = []

        for bb in basic_blocks:
            self._process_basic_block(bb)
