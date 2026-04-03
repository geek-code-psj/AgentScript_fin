from __future__ import annotations

import pytest

from agentscript.compiler.errors import SemanticError
from agentscript.compiler.ir import OpCode, format_ir, lower_source
from agentscript.compiler.semantics import analyze_source, format_semantic_model


def test_semantic_analysis_resolves_type_aliases_and_llm_native_fields() -> None:
    source = """
    type LegalSources = list[Citation]

    tool search_law(query: string) -> LegalSources
    workflow inspect(claim: Claim, query: string) -> float {
      let sources: LegalSources = search_law(query)
      return claim.confidence
    }
    """
    model = analyze_source(source)

    assert model.type_aliases["LegalSources"].display() == "list[Citation]"
    assert model.workflows["inspect"].return_type.display() == "float"

    summary = format_semantic_model(model)
    assert "LegalSources = list[Citation]" in summary
    assert "inspect(claim: Claim, query: string) -> float" in summary


def test_semantic_analysis_catches_visible_type_errors() -> None:
    source = """
    workflow bad() -> Citation {
      let source: Citation = "not a citation"
      return source
    }
    """
    with pytest.raises(SemanticError) as exc_info:
        analyze_source(source)

    message = str(exc_info.value)
    assert "TypeError: Expected Citation, got string" in message
    assert "Hint:" in message


def test_semantic_analysis_rejects_unknown_step_tools() -> None:
    source = """
    workflow bad(query: string) -> string {
      step fetch using missing_tool(query)
      return "done"
    }
    """
    with pytest.raises(SemanticError, match="Unknown tool 'missing_tool'"):
        analyze_source(source)


def test_ir_lowering_emits_tool_calls_and_removes_dead_code() -> None:
    source = """
    tool search_law(query: string) -> list[Citation]
    tool summarize_claim(citations: list[Citation]) -> Claim

    workflow legal_brief(query: string) -> Claim {
      let sources: list[Citation] = search_law(query)
      return summarize_claim(sources)
      let dead: string = "never reached"
    }
    """
    ir_program = lower_source(source)
    workflow = ir_program.workflows[0]

    assert any(
        instruction.opcode is OpCode.TOOL_CALL for instruction in workflow.instructions
    )
    assert any(
        instruction.opcode is OpCode.TOOL_RESULT for instruction in workflow.instructions
    )
    assert any(
        instruction.opcode is OpCode.MEM_SET for instruction in workflow.instructions
    )
    assert all(
        not (
            instruction.opcode is OpCode.STORE_NAME
            and instruction.args[0] == "dead"
        )
        for instruction in workflow.instructions
    )

    rendered = format_ir(ir_program)
    assert "TOOL_CALL" in rendered
    assert "TOOL_RESULT" in rendered
    assert "RETURN" in rendered


def test_ir_lowering_emits_control_flow_for_if_else() -> None:
    source = """
    tool approve(query: string) -> Claim
    tool reject(query: string) -> Claim

    workflow decide(flag: bool, query: string) -> Claim {
      if flag {
        return approve(query)
      } else {
        return reject(query)
      }
    }
    """
    workflow = lower_source(source).workflows[0]
    opcodes = [instruction.opcode for instruction in workflow.instructions]

    assert OpCode.JUMP_IF_FALSE in opcodes
    assert OpCode.LABEL in opcodes
    assert opcodes.count(OpCode.RETURN) == 2


def test_semantics_and_ir_support_builtin_mem_search() -> None:
    source = """
    workflow recall(query: string) -> list[MemoryEntry] {
      let note: string = "BNS section 103 theft"
      return mem_search(query)
    }
    """
    model = analyze_source(source)
    assert model.workflows["recall"].return_type.display() == "list[MemoryEntry]"

    workflow = lower_source(source).workflows[0]
    assert any(
        instruction.opcode is OpCode.MEM_SEARCH for instruction in workflow.instructions
    )


def test_semantic_analysis_exposes_step_results_to_later_bindings() -> None:
    source = """
    tool search_law(query: string) -> list[Citation]
    tool summarize_claim(citations: list[Citation]) -> Claim

    workflow legal_brief(query: string) -> Claim {
      step sources using search_law(query)
      return summarize_claim(sources)
    }
    """
    model = analyze_source(source)

    assert model.workflows["legal_brief"].return_type.display() == "Claim"
