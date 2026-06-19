"""Tests for the knowledge graph components:
- GoldenModelParser  (gm_parser.py)
- CoverageParser     (cov_parser.py)
- TypeResolver       (type_resolver.py)
- build_knowledge_graph (kg_builder.py)
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths to real testbench files (available in the workspace)
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[1]
_HSS_GM = (
    _ROOT
    / "testbench/tb/ifx_car_sbc_dig_mvc/sv/include/ifx_car_sbc_dig_mvc_scb_hss_golden_model.svh"
)
_HSS_COV = (
    _ROOT
    / "testbench/tb/ifx_car_sbc_dig_mvc/sv/include/ifx_car_sbc_dig_mvc_scb_hss_coverage.svh"
)
_COMMON = (
    _ROOT / "testbench/tb/ifx_car_sbc_dig_mvc/sv/include/ifx_car_sbc_dig_mvc_common.svh"
)
_SFR_ENUMS = (
    _ROOT / "testbench/tb_gen/regmodel/sbc_unit_user/sbc_unit_user_sfr_enums.svh"
)
_SFR_CG_ENUMS = (
    _ROOT / "testbench/tb_gen/regmodel/sbc_unit_user/sbc_unit_user_sfr_cg_enums.svh"
)

_REAL_FILES_AVAILABLE = _HSS_GM.exists() and _HSS_COV.exists()
_SHARED_FILES_AVAILABLE = (
    _COMMON.exists() and _SFR_ENUMS.exists() and _SFR_CG_ENUMS.exists()
)

skip_no_tb = pytest.mark.skipif(
    not _REAL_FILES_AVAILABLE,
    reason="Testbench files not present in this environment",
)
skip_no_shared = pytest.mark.skipif(
    not _SHARED_FILES_AVAILABLE,
    reason="Shared context files not present in this environment",
)


# ===========================================================================
# GoldenModelParser
# ===========================================================================


class TestGoldenModelParser:
    """Unit tests using inline SV content (no file I/O except tmp_path)."""

    def _write(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "gm.svh"
        p.write_text(textwrap.dedent(content), encoding="utf-8")
        return p

    def test_class_name_detected(self, tmp_path):
        from src.knowledge_graph.gm_parser import GoldenModelParser

        f = self._write(
            tmp_path,
            """\
            class my_hss_gm extends uvm_component;
              bit some_var;
            endclass
        """,
        )
        ast = GoldenModelParser(f).parse()
        assert ast.class_name == "my_hss_gm"

    def test_variable_parsed_with_type_and_comment(self, tmp_path):
        from src.knowledge_graph.gm_parser import GoldenModelParser

        f = self._write(
            tmp_path,
            """\
            class gm;
              bit hss1_en; // Enable output for HSS1
            endclass
        """,
        )
        ast = GoldenModelParser(f).parse()
        var = next(v for v in ast.variables if v.name == "hss1_en")
        assert var.sv_type == "bit"
        assert "HSS1" in var.comment

    def test_register_trace_extracted_from_comment(self, tmp_path):
        from src.knowledge_graph.gm_parser import GoldenModelParser

        f = self._write(
            tmp_path,
            """\
            class gm;
              MY_ENUM field_hss1_cfg = MY_ENUM_SETTING1; // value of the field HW_CTRL2.WK1_HS1_CFG
            endclass
        """,
        )
        ast = GoldenModelParser(f).parse()
        var = next(v for v in ast.variables if v.name == "field_hss1_cfg")
        assert var.register_trace == "HW_CTRL2.WK1_HS1_CFG"

    def test_variable_group_tracked_from_section_comment(self, tmp_path):
        from src.knowledge_graph.gm_parser import GoldenModelParser

        f = self._write(
            tmp_path,
            """\
            class gm;
              /////// HSS modes ///////
              bit hss_mode_var;
            endclass
        """,
        )
        ast = GoldenModelParser(f).parse()
        var = next(v for v in ast.variables if v.name == "hss_mode_var")
        assert "HSS modes" in var.variable_group

    def test_event_parsed_with_comment(self, tmp_path):
        from src.knowledge_graph.gm_parser import GoldenModelParser

        f = self._write(
            tmp_path,
            """\
            class gm;
              event hss1_oc_e; // triggered when HSS1 overcurrent is true
            endclass
        """,
        )
        ast = GoldenModelParser(f).parse()
        evt = next(e for e in ast.events if e.name == "hss1_oc_e")
        assert "overcurrent" in evt.comment

    def test_task_detected(self, tmp_path):
        from src.knowledge_graph.gm_parser import GoldenModelParser

        f = self._write(
            tmp_path,
            """\
            class gm;
              task run_hss_update();
                hss1_mode = HSS_ON;
              endtask
            endclass
        """,
        )
        ast = GoldenModelParser(f).parse()
        task = next(t for t in ast.tasks if t.name == "run_hss_update")
        assert task.kind == "task"

    def test_function_detected(self, tmp_path):
        from src.knowledge_graph.gm_parser import GoldenModelParser

        f = self._write(
            tmp_path,
            """\
            class gm;
              function bit is_valid(int x);
                return x > 0;
              endfunction
            endclass
        """,
        )
        ast = GoldenModelParser(f).parse()
        func = next(t for t in ast.tasks if t.name == "is_valid")
        assert func.kind == "function"

    def test_assignment_links_variable_to_task(self, tmp_path):
        from src.knowledge_graph.gm_parser import GoldenModelParser

        f = self._write(
            tmp_path,
            """\
            class gm;
              bit hss1_en;
              task set_enable();
                hss1_en = 1;
              endtask
            endclass
        """,
        )
        ast = GoldenModelParser(f).parse()
        var = next(v for v in ast.variables if v.name == "hss1_en")
        assert "task:set_enable" in var.assigned_by_tasks

    def test_cross_model_ref_extracted(self, tmp_path):
        from src.knowledge_graph.gm_parser import GoldenModelParser

        f = self._write(
            tmp_path,
            """\
            class gm;
              task check();
                if (p_scb.fsm_gm.fsm_current_state == FSM_NORMAL_MODE) begin
                  p_scb.wk_gm.cyclic_wk_timer_cfg_gm = 1;
                end
              endtask
            endclass
        """,
        )
        ast = GoldenModelParser(f).parse()
        paths = {cr.full_path for cr in ast.cross_refs}
        assert "p_scb.fsm_gm.fsm_current_state" in paths
        assert "p_scb.wk_gm.cyclic_wk_timer_cfg_gm" in paths

    def test_cross_model_ref_wp_token_parsed(self, tmp_path):
        from src.knowledge_graph.gm_parser import GoldenModelParser

        f = self._write(
            tmp_path,
            """\
            class gm;
              task t();
                if (p_scb.fsm_gm.fsm_current_state) begin end
              endtask
            endclass
        """,
        )
        ast = GoldenModelParser(f).parse()
        ref = next(cr for cr in ast.cross_refs if cr.wp_token == "fsm")
        assert ref.var_name == "fsm_current_state"

    def test_sv_keywords_not_parsed_as_variables(self, tmp_path):
        from src.knowledge_graph.gm_parser import GoldenModelParser

        f = self._write(
            tmp_path,
            """\
            class gm;
              task t();
                if (x) begin
                  int i = 0;
                end
              endtask
            endclass
        """,
        )
        ast = GoldenModelParser(f).parse()
        names = {v.name for v in ast.variables}
        assert "if" not in names
        assert "begin" not in names
        assert "end" not in names

    def test_no_register_trace_when_absent(self, tmp_path):
        from src.knowledge_graph.gm_parser import GoldenModelParser

        f = self._write(
            tmp_path,
            """\
            class gm;
              bit plain_flag; // just a flag
            endclass
        """,
        )
        ast = GoldenModelParser(f).parse()
        var = next(v for v in ast.variables if v.name == "plain_flag")
        assert var.register_trace == ""

    @skip_no_tb
    def test_hss_golden_model_class_name(self):
        from src.knowledge_graph.gm_parser import GoldenModelParser

        ast = GoldenModelParser(_HSS_GM).parse()
        assert ast.class_name == "ifx_car_sbc_dig_mvc_scb_hss_golden_model"

    @skip_no_tb
    def test_hss_golden_model_variable_count(self):
        from src.knowledge_graph.gm_parser import GoldenModelParser

        ast = GoldenModelParser(_HSS_GM).parse()
        assert len(ast.variables) > 50, "Expected many variable declarations"

    @skip_no_tb
    def test_hss_golden_model_hss1_mode_found(self):
        from src.knowledge_graph.gm_parser import GoldenModelParser

        ast = GoldenModelParser(_HSS_GM).parse()
        var = next((v for v in ast.variables if v.name == "hss1_mode"), None)
        assert var is not None
        assert "hss_mode" in var.sv_type.lower() or "hss_mode" in var.sv_type

    @skip_no_tb
    def test_hss_golden_model_register_traces_present(self):
        from src.knowledge_graph.gm_parser import GoldenModelParser

        ast = GoldenModelParser(_HSS_GM).parse()
        traced = [v for v in ast.variables if v.register_trace]
        assert len(traced) > 5, "Expected multiple register-traced variables"

    @skip_no_tb
    def test_hss_golden_model_events_found(self):
        from src.knowledge_graph.gm_parser import GoldenModelParser

        ast = GoldenModelParser(_HSS_GM).parse()
        assert len(ast.events) >= 6

    @skip_no_tb
    def test_hss_golden_model_tasks_found(self):
        from src.knowledge_graph.gm_parser import GoldenModelParser

        ast = GoldenModelParser(_HSS_GM).parse()
        assert len(ast.tasks) >= 5

    @skip_no_tb
    def test_hss_golden_model_cross_refs_found(self):
        from src.knowledge_graph.gm_parser import GoldenModelParser

        ast = GoldenModelParser(_HSS_GM).parse()
        paths = {cr.full_path for cr in ast.cross_refs}
        assert "p_scb.fsm_gm.fsm_current_state" in paths


# ===========================================================================
# CoverageParser
# ===========================================================================


class TestCoverageParser:
    """Unit tests using inline SV content."""

    def _write(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "cov.svh"
        p.write_text(textwrap.dedent(content), encoding="utf-8")
        return p

    def test_covergroup_name_parsed(self, tmp_path):
        from src.knowledge_graph.cov_parser import CoverageParser

        f = self._write(
            tmp_path,
            """\
            covergroup dscov_HSS_01_mode_change with function sample();
              option.name = "dscov_HSS_01_mode_change";
              SBC_MODE_cp: coverpoint sbc_mode { bins A = {1}; }
            endgroup
        """,
        )
        cgs = CoverageParser(f).parse()
        assert len(cgs) == 1
        assert cgs[0].name == "dscov_HSS_01_mode_change"

    def test_explicit_sample_args_parsed(self, tmp_path):
        from src.knowledge_graph.cov_parser import CoverageParser

        f = self._write(
            tmp_path,
            """\
            covergroup my_cg with function sample(my_mode_t hss1_mode, bit flag);
              CP: coverpoint hss1_mode { bins A = {1}; }
            endgroup
        """,
        )
        cgs = CoverageParser(f).parse()
        assert "hss1_mode" in cgs[0].sample_args
        assert "flag" in cgs[0].sample_args

    def test_implicit_sample_no_args(self, tmp_path):
        from src.knowledge_graph.cov_parser import CoverageParser

        f = self._write(
            tmp_path,
            """\
            covergroup my_cg with function sample();
              CP: coverpoint some_var { bins A = {1}; }
            endgroup
        """,
        )
        cgs = CoverageParser(f).parse()
        assert cgs[0].sample_args == []

    def test_coverpoint_expression_extracted(self, tmp_path):
        from src.knowledge_graph.cov_parser import CoverageParser

        f = self._write(
            tmp_path,
            """\
            covergroup my_cg with function sample();
              SBC_MODE_cp: coverpoint p_scb.fsm_gm.fsm_current_state {
                bins NORMAL = {FSM_NORMAL_MODE};
              }
            endgroup
        """,
        )
        cgs = CoverageParser(f).parse()
        cp = cgs[0].coverpoints[0]
        assert cp.name == "SBC_MODE_cp"
        assert "p_scb.fsm_gm.fsm_current_state" in cp.expression

    def test_cross_declaration_parsed(self, tmp_path):
        from src.knowledge_graph.cov_parser import CoverageParser

        f = self._write(
            tmp_path,
            """\
            covergroup my_cg with function sample();
              A_cp: coverpoint var_a { bins X = {1}; }
              B_cp: coverpoint var_b { bins Y = {2}; }
              AB_crs: cross A_cp, B_cp;
            endgroup
        """,
        )
        cgs = CoverageParser(f).parse()
        crs = cgs[0].crosses[0]
        assert crs.name == "AB_crs"
        assert "A_cp" in crs.coverpoints
        assert "B_cp" in crs.coverpoints

    def test_sampling_trigger_extracted_from_task(self, tmp_path):
        from src.knowledge_graph.cov_parser import CoverageParser

        f = self._write(
            tmp_path,
            """\
            covergroup my_cg with function sample();
              CP: coverpoint my_var { bins A = {1}; }
            endgroup

            task sample_my_cg();
              forever @(my_var, other_var) begin
                my_cg.sample();
              end
            endtask
        """,
        )
        cgs = CoverageParser(f).parse()
        assert "@(my_var, other_var)" in cgs[0].sampling_trigger_expression

    def test_trigger_variables_extracted(self, tmp_path):
        from src.knowledge_graph.cov_parser import CoverageParser

        f = self._write(
            tmp_path,
            """\
            covergroup my_cg with function sample();
              CP: coverpoint a { bins X = {1}; }
            endgroup
            task sample_my_cg();
              forever @(var_a, var_b, p_scb.fsm_gm.fsm_state) begin
                my_cg.sample();
              end
            endtask
        """,
        )
        cgs = CoverageParser(f).parse()
        tv = cgs[0].trigger_variables
        assert "var_a" in tv
        assert "var_b" in tv
        assert "p_scb.fsm_gm.fsm_state" in tv

    def test_posedge_not_in_trigger_variables(self, tmp_path):
        from src.knowledge_graph.cov_parser import CoverageParser

        f = self._write(
            tmp_path,
            """\
            covergroup my_cg with function sample();
              CP: coverpoint sig { bins A = {1}; }
            endgroup
            task sample_my_cg();
              forever @(posedge clk, negedge rst) begin
                my_cg.sample();
              end
            endtask
        """,
        )
        cgs = CoverageParser(f).parse()
        tv = cgs[0].trigger_variables
        assert "posedge" not in tv
        assert "negedge" not in tv

    def test_sampled_variables_include_coverpoint_expressions(self, tmp_path):
        from src.knowledge_graph.cov_parser import CoverageParser

        f = self._write(
            tmp_path,
            """\
            covergroup my_cg with function sample();
              MODE_cp: coverpoint hss1_mode { bins A = {1}; }
              STATE_cp: coverpoint sbc_state { bins B = {2}; }
            endgroup
        """,
        )
        cgs = CoverageParser(f).parse()
        sv = cgs[0].sampled_variables
        assert "hss1_mode" in sv
        assert "sbc_state" in sv

    def test_multiple_covergroups_parsed(self, tmp_path):
        from src.knowledge_graph.cov_parser import CoverageParser

        f = self._write(
            tmp_path,
            """\
            covergroup cg_one with function sample();
              CP: coverpoint v1 { bins A = {1}; }
            endgroup
            covergroup cg_two with function sample();
              CP: coverpoint v2 { bins B = {2}; }
            endgroup
        """,
        )
        cgs = CoverageParser(f).parse()
        assert len(cgs) == 2
        names = {cg.name for cg in cgs}
        assert "cg_one" in names
        assert "cg_two" in names

    def test_no_trigger_when_no_sample_task(self, tmp_path):
        from src.knowledge_graph.cov_parser import CoverageParser

        f = self._write(
            tmp_path,
            """\
            covergroup lone_cg with function sample();
              CP: coverpoint x { bins A = {1}; }
            endgroup
        """,
        )
        cgs = CoverageParser(f).parse()
        assert cgs[0].sampling_trigger_expression == ""
        assert cgs[0].trigger_variables == []

    @skip_no_tb
    def test_hss_coverage_file_covergroup_count(self):
        from src.knowledge_graph.cov_parser import CoverageParser

        cgs = CoverageParser(_HSS_COV).parse()
        assert len(cgs) >= 10, "Expected at least 10 covergroups in HSS coverage file"

    @skip_no_tb
    def test_hss_cg01_trigger_present(self):
        from src.knowledge_graph.cov_parser import CoverageParser

        cgs = CoverageParser(_HSS_COV).parse()
        cg01 = next(c for c in cgs if c.name == "dscov_HSS_01_mode_change")
        assert "hss1_mode" in cg01.sampling_trigger_expression
        assert "hss1_mode" in cg01.trigger_variables

    @skip_no_tb
    def test_hss_cg02_explicit_sample_args(self):
        from src.knowledge_graph.cov_parser import CoverageParser

        cgs = CoverageParser(_HSS_COV).parse()
        cg02 = next(c for c in cgs if c.name == "dscov_HSS_02_timer_valid_config")
        assert "hss1_mode" in cg02.sample_args
        assert "field_timer1_ont" in cg02.sample_args

    @skip_no_tb
    def test_hss_covergroups_have_coverpoints(self):
        from src.knowledge_graph.cov_parser import CoverageParser

        cgs = CoverageParser(_HSS_COV).parse()
        for cg in cgs:
            assert len(cg.coverpoints) > 0, f"{cg.name} has no coverpoints"

    @skip_no_tb
    def test_hss_cross_model_ref_in_trigger_vars(self):
        from src.knowledge_graph.cov_parser import CoverageParser

        cgs = CoverageParser(_HSS_COV).parse()
        cg01 = next(c for c in cgs if c.name == "dscov_HSS_01_mode_change")
        assert "p_scb.fsm_gm.fsm_current_state" in cg01.trigger_variables


# ===========================================================================
# TypeResolver
# ===========================================================================


class TestTypeResolver:
    """Unit tests using inline SV content."""

    def _write_common(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "common.svh"
        p.write_text(textwrap.dedent(content), encoding="utf-8")
        return p

    def _write_sfr(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "sfr.svh"
        p.write_text(textwrap.dedent(content), encoding="utf-8")
        return p

    def test_typedef_enum_members_parsed(self, tmp_path):
        from src.knowledge_graph.type_resolver import TypeResolver

        f = self._write_common(
            tmp_path,
            """\
            typedef enum { HSS_OFF, HSS_ON, HSS_TIMER1, HSS_TIMER2, HSS_PWM } ifx_car_sbc_hss_mode_t;
        """,
        )
        tr = TypeResolver(common_file=f)
        td = tr.resolve_type("ifx_car_sbc_hss_mode_t")
        assert td is not None
        assert td["kind"] == "enum"
        assert "HSS_OFF" in td["members"]
        assert "HSS_PWM" in td["members"]
        assert len(td["members"]) == 5

    def test_typedef_enum_with_base_type(self, tmp_path):
        from src.knowledge_graph.type_resolver import TypeResolver

        f = self._write_common(
            tmp_path,
            """\
            typedef enum logic [1:0] {
              CAN_OFF = 2'b00,
              CAN_NORMAL = 2'b11
            } ifx_car_sbc_can_mode_t;
        """,
        )
        tr = TypeResolver(common_file=f)
        td = tr.resolve_type("ifx_car_sbc_can_mode_t")
        assert "CAN_OFF" in td["members"]
        assert "CAN_NORMAL" in td["members"]

    def test_typedef_member_comments_captured(self, tmp_path):
        from src.knowledge_graph.type_resolver import TypeResolver

        f = self._write_common(
            tmp_path,
            """\
            typedef enum int {
              CAN_WAKE_OFF   = 0,
              CAN_WAKE_WUP   = 1 // wake up pattern
            } ifx_car_sbc_can_wake_mode_t;
        """,
        )
        tr = TypeResolver(common_file=f)
        td = tr.resolve_type("ifx_car_sbc_can_wake_mode_t")
        assert "CAN_WAKE_WUP" in td["member_comments"]
        assert "wake up pattern" in td["member_comments"]["CAN_WAKE_WUP"]

    def test_resolve_type_returns_none_for_unknown(self, tmp_path):
        from src.knowledge_graph.type_resolver import TypeResolver

        f = self._write_common(
            tmp_path,
            """\
            typedef enum { A, B } my_t;
        """,
        )
        tr = TypeResolver(common_file=f)
        assert tr.resolve_type("nonexistent_type_t") is None

    def test_register_enum_hex_values_converted(self, tmp_path):
        from src.knowledge_graph.type_resolver import TypeResolver

        f = self._write_sfr(
            tmp_path,
            """\
            typedef enum int {
                TIMER1_CTRL_TIMER1_ON_SETTING1 = 'h0,
                TIMER1_CTRL_TIMER1_ON_SETTING2 = 'h1,
                TIMER1_CTRL_TIMER1_ON_SETTING16 = 'hF
            } TIMER1_CTRL_TIMER1_ON_ENUM;
        """,
        )
        tr = TypeResolver(sfr_enums_file=f)
        re_def = tr.resolve_register_enum("TIMER1_CTRL_TIMER1_ON_ENUM")
        assert re_def is not None
        assert re_def["values"]["TIMER1_CTRL_TIMER1_ON_SETTING1"] == 0
        assert re_def["values"]["TIMER1_CTRL_TIMER1_ON_SETTING2"] == 1
        assert re_def["values"]["TIMER1_CTRL_TIMER1_ON_SETTING16"] == 15

    def test_register_enum_returns_none_for_unknown(self, tmp_path):
        from src.knowledge_graph.type_resolver import TypeResolver

        f = self._write_sfr(
            tmp_path,
            """\
            typedef enum int { X = 'h0 } MY_ENUM;
        """,
        )
        tr = TypeResolver(sfr_enums_file=f)
        assert tr.resolve_register_enum("NONEXISTENT_ENUM") is None

    def test_is_register_enum_true_for_known(self, tmp_path):
        from src.knowledge_graph.type_resolver import TypeResolver

        f = self._write_sfr(
            tmp_path,
            """\
            typedef enum int { X = 'h0 } MY_ENUM;
        """,
        )
        tr = TypeResolver(sfr_enums_file=f)
        assert tr.is_register_enum("MY_ENUM") is True

    def test_is_register_enum_false_for_unknown(self, tmp_path):
        from src.knowledge_graph.type_resolver import TypeResolver

        f = self._write_sfr(
            tmp_path,
            """\
            typedef enum int { X = 'h0 } MY_ENUM;
        """,
        )
        tr = TypeResolver(sfr_enums_file=f)
        assert tr.is_register_enum("OTHER_ENUM") is False

    def test_counts_reported_correctly(self, tmp_path):
        from src.knowledge_graph.type_resolver import TypeResolver

        cf = self._write_common(
            tmp_path / "c.svh" if False else tmp_path,
            """\
            typedef enum { A, B } type_a_t;
            typedef enum { C, D } type_b_t;
        """,
        )
        # write sfr in a separate tmp subdir
        sfr_dir = tmp_path / "sfr"
        sfr_dir.mkdir()
        sf = sfr_dir / "sfr.svh"
        sf.write_text(
            "typedef enum int { X = 'h0 } ENUM_A;\ntypedef enum int { Y = 'h1 } ENUM_B;\n"
        )
        tr = TypeResolver(common_file=cf, sfr_enums_file=sf)
        assert tr.type_def_count == 2
        assert tr.register_enum_count == 2

    def test_works_without_sfr_file(self, tmp_path):
        from src.knowledge_graph.type_resolver import TypeResolver

        f = self._write_common(
            tmp_path,
            """\
            typedef enum { A, B } my_t;
        """,
        )
        tr = TypeResolver(common_file=f)  # no sfr_enums_file
        assert tr.resolve_register_enum("anything") is None
        assert tr.register_enum_count == 0

    @skip_no_shared
    def test_real_hss_mode_type_resolved(self):
        from src.knowledge_graph.type_resolver import TypeResolver

        tr = TypeResolver(common_file=_COMMON)
        td = tr.resolve_type("ifx_car_sbc_hss_mode_t")
        assert td is not None
        assert "HSS_ON" in td["members"]
        assert "HSS_PWM" in td["members"]

    @skip_no_shared
    def test_real_fsm_mode_type_resolved(self):
        from src.knowledge_graph.type_resolver import TypeResolver

        tr = TypeResolver(common_file=_COMMON)
        td = tr.resolve_type("ifx_car_sbc_fsm_mode_t")
        assert td is not None
        assert "FSM_NORMAL_MODE" in td["members"]

    @skip_no_shared
    def test_real_register_enum_values_present(self):
        from src.knowledge_graph.type_resolver import TypeResolver

        tr = TypeResolver(sfr_enums_files=[_SFR_ENUMS, _SFR_CG_ENUMS])
        re_def = tr.resolve_register_enum("TIMER1_CTRL_TIMER1_ON_ENUM")
        assert re_def is not None
        assert "TIMER1_CTRL_TIMER1_ON_SETTING1" in re_def["values"]
        assert re_def["values"]["TIMER1_CTRL_TIMER1_ON_SETTING1"] == 0

    @skip_no_shared
    def test_real_shared_files_type_def_count(self):
        from src.knowledge_graph.type_resolver import TypeResolver

        tr = TypeResolver(common_file=_COMMON, sfr_enums_file=_SFR_ENUMS)
        assert tr.type_def_count >= 5
        assert tr.register_enum_count >= 10


# ===========================================================================
# KGBuilder (build_knowledge_graph)
# ===========================================================================


class TestKGBuilder:

    def test_unknown_wp_raises_value_error(self):
        from src.knowledge_graph.kg_builder import build_knowledge_graph

        with pytest.raises((ValueError, KeyError, Exception)):
            build_knowledge_graph("NONEXISTENT_WP_XYZ")

    @skip_no_tb
    def test_hss_graph_structure_keys(self):
        from src.knowledge_graph.kg_builder import build_knowledge_graph

        g = build_knowledge_graph("HSS")
        assert g["wp"] == "HSS"
        assert "generated_at" in g
        assert "source_files" in g
        assert "reference_model" in g
        assert "nodes" in g
        assert "edges" in g

    @skip_no_tb
    def test_hss_graph_node_sections_present(self):
        from src.knowledge_graph.kg_builder import build_knowledge_graph

        g = build_knowledge_graph("HSS")
        nodes = g["nodes"]
        assert "variables" in nodes
        assert "events" in nodes
        assert "tasks" in nodes
        assert "covergroups" in nodes

    @skip_no_tb
    def test_hss_graph_variable_count(self):
        from src.knowledge_graph.kg_builder import build_knowledge_graph

        g = build_knowledge_graph("HSS")
        assert len(g["nodes"]["variables"]) > 50

    @skip_no_tb
    def test_hss_graph_covergroup_count(self):
        from src.knowledge_graph.kg_builder import build_knowledge_graph

        g = build_knowledge_graph("HSS")
        assert len(g["nodes"]["covergroups"]) >= 10

    @skip_no_tb
    def test_hss_graph_edges_present(self):
        from src.knowledge_graph.kg_builder import build_knowledge_graph

        g = build_knowledge_graph("HSS")
        assert len(g["edges"]) > 50

    @skip_no_tb
    def test_hss_graph_sampled_on_edges_exist(self):
        from src.knowledge_graph.kg_builder import build_knowledge_graph

        g = build_knowledge_graph("HSS")
        sampled_on = [e for e in g["edges"] if e["rel"] == "sampled_on"]
        assert len(sampled_on) > 0

    @skip_no_tb
    def test_hss_graph_mirrors_register_edges_exist(self):
        from src.knowledge_graph.kg_builder import build_knowledge_graph

        g = build_knowledge_graph("HSS")
        reg_edges = [e for e in g["edges"] if e["rel"] == "mirrors_register"]
        assert len(reg_edges) > 5

    @skip_no_tb
    def test_hss_graph_updates_edges_exist(self):
        from src.knowledge_graph.kg_builder import build_knowledge_graph

        g = build_knowledge_graph("HSS")
        update_edges = [e for e in g["edges"] if e["rel"] == "updates"]
        assert len(update_edges) > 0

    @skip_no_tb
    def test_hss_graph_samples_edges_exist(self):
        from src.knowledge_graph.kg_builder import build_knowledge_graph

        g = build_knowledge_graph("HSS")
        sample_edges = [e for e in g["edges"] if e["rel"] == "samples"]
        assert len(sample_edges) > 0

    @skip_no_tb
    def test_hss_graph_edges_deduplicated(self):
        from src.knowledge_graph.kg_builder import build_knowledge_graph

        g = build_knowledge_graph("HSS")
        tuples = [(e["from"], e["rel"], e["to"]) for e in g["edges"]]
        assert len(tuples) == len(set(tuples)), "Duplicate edges found"

    @skip_no_tb
    def test_hss_variable_node_has_id_field(self):
        from src.knowledge_graph.kg_builder import build_knowledge_graph

        g = build_knowledge_graph("HSS")
        for var in g["nodes"]["variables"]:
            assert "id" in var, f"Variable {var.get('name')} missing 'id'"
            assert var["id"].startswith("var:")

    @skip_no_tb
    @skip_no_shared
    def test_hss_mode_variable_has_type_definition(self):
        from src.knowledge_graph.kg_builder import build_knowledge_graph

        g = build_knowledge_graph("HSS")
        hss1_mode = next(v for v in g["nodes"]["variables"] if v["name"] == "hss1_mode")
        assert "type_definition" in hss1_mode
        assert "HSS_ON" in hss1_mode["type_definition"]["members"]

    @skip_no_tb
    @skip_no_shared
    def test_register_field_variable_has_register_enum(self):
        from src.knowledge_graph.kg_builder import build_knowledge_graph

        g = build_knowledge_graph("HSS")
        field_var = next(
            (v for v in g["nodes"]["variables"] if v["name"] == "field_timer1_ont"),
            None,
        )
        assert field_var is not None, "field_timer1_ont not found"
        assert "register_enum" in field_var
        assert "values" in field_var["register_enum"]

    @skip_no_tb
    def test_hss_cross_model_variables_resolved(self):
        from src.knowledge_graph.kg_builder import build_knowledge_graph

        g = build_knowledge_graph("HSS")
        ids = {v["id"] for v in g["nodes"]["variables"]}
        assert "var:p_scb.fsm_gm.fsm_current_state" in ids

    @skip_no_tb
    def test_hss_graph_reference_model_class_name(self):
        from src.knowledge_graph.kg_builder import build_knowledge_graph

        g = build_knowledge_graph("HSS")
        assert (
            g["reference_model"]["class_name"]
            == "ifx_car_sbc_dig_mvc_scb_hss_golden_model"
        )

    @skip_no_tb
    def test_hss_covergroup_nodes_have_semantic_summary_field(self):
        from src.knowledge_graph.kg_builder import build_knowledge_graph

        g = build_knowledge_graph("HSS")
        for cg in g["nodes"]["covergroups"]:
            assert (
                "semantic_summary" in cg
            ), f"{cg['name']} missing semantic_summary field"

    @skip_no_tb
    def test_hss_variable_nodes_have_semantic_summary_field(self):
        from src.knowledge_graph.kg_builder import build_knowledge_graph

        g = build_knowledge_graph("HSS")
        for var in g["nodes"]["variables"]:
            assert (
                "semantic_summary" in var
            ), f"{var.get('name')} missing semantic_summary field"
