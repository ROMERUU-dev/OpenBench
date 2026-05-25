"""Tests for openbench.filters SOFIA integration."""

from __future__ import annotations

import numpy as np
import pytest

from openbench.filters import (
    Approximation,
    DesignInputs,
    FilterDesigner,
    FilterKind,
    FilterSpec,
    FilterValidator,
    Topology,
    design_filter,
)


def _lp_butterworth_inputs(
    passband_hz: float = 1_000.0,
    stopband_hz: float = 5_000.0,
) -> DesignInputs:
    return DesignInputs(
        kind=FilterKind.LOWPASS,
        approximation=Approximation.BUTTERWORTH,
        spec=FilterSpec(passband_hz=passband_hz, stopband_hz=stopband_hz),
        passband_ripple_db=1.0,
        stopband_attenuation_db=40.0,
        topology=Topology.SALLEN_KEY,
    )


def _hp_butterworth_inputs() -> DesignInputs:
    return DesignInputs(
        kind=FilterKind.HIGHPASS,
        approximation=Approximation.BUTTERWORTH,
        spec=FilterSpec(passband_hz=2_000.0, stopband_hz=500.0),
        passband_ripple_db=1.0,
        stopband_attenuation_db=40.0,
        topology=Topology.SALLEN_KEY,
    )


class TestFilterDesigner:
    def test_design_returns_result_with_stages(self) -> None:
        inputs = _lp_butterworth_inputs()
        result = FilterDesigner(inputs).design()
        assert result.order >= 2
        assert len(result.stages) >= 1

    def test_design_order_sufficient_for_attenuation(self) -> None:
        inputs = _lp_butterworth_inputs(passband_hz=1_000.0, stopband_hz=2_000.0)
        result = FilterDesigner(inputs).design()
        assert result.order >= 4

    def test_render_netlist_contains_spice_header(self) -> None:
        inputs = _lp_butterworth_inputs()
        designer = FilterDesigner(inputs)
        result = designer.design()
        netlist = designer.render_netlist(result)
        assert "SOFIA" in netlist
        assert ".end" in netlist

    def test_format_result_is_valid_json(self) -> None:
        import json

        inputs = _lp_butterworth_inputs()
        designer = FilterDesigner(inputs)
        result = designer.design()
        parsed = json.loads(designer.format_result(result))
        assert parsed["order"] == result.order

    def test_measurement_setup_covers_passband(self) -> None:
        inputs = _lp_butterworth_inputs(passband_hz=1_000.0)
        designer = FilterDesigner(inputs)
        result = designer.design()
        setup = designer.measurement_setup(result)
        assert setup.start_hz < 1_000.0
        assert setup.stop_hz > 1_000.0
        assert setup.num_points > 0
        assert setup.excitation_v > 0

    def test_convenience_design_filter_function(self) -> None:
        result = design_filter(_lp_butterworth_inputs())
        assert result.order >= 2


class TestFilterValidator:
    def test_theoretical_response_lowpass_attenuates_stopband(self) -> None:
        inputs = _lp_butterworth_inputs(passband_hz=1_000.0, stopband_hz=5_000.0)
        result = FilterDesigner(inputs).design()
        validator = FilterValidator(result, inputs)

        freqs = np.array([100.0, 1_000.0, 5_000.0, 10_000.0])
        mag_db, _ = validator.theoretical_response(freqs)

        # Near-DC should be close to 0 dB, stopband should be well attenuated.
        assert mag_db[0] > -3.0, "passband should be flat near DC"
        assert mag_db[-1] < -30.0, "stopband should be strongly attenuated"

    def test_theoretical_response_highpass_attenuates_near_dc(self) -> None:
        inputs = _hp_butterworth_inputs()
        result = FilterDesigner(inputs).design()
        validator = FilterValidator(result, inputs)

        freqs = np.array([50.0, 500.0, 5_000.0, 50_000.0])
        mag_db, _ = validator.theoretical_response(freqs)

        assert mag_db[0] < -20.0, "HP must attenuate near DC"
        assert mag_db[-1] > -3.0, "HP should pass high frequencies"

    def test_validate_with_measurements_computes_rms_error(self) -> None:
        inputs = _lp_butterworth_inputs()
        result = FilterDesigner(inputs).design()
        validator = FilterValidator(result, inputs)

        freqs = np.logspace(2, 4, 50)
        mag_theory, _ = validator.theoretical_response(freqs)
        # Perturb by +1 dB to simulate a systematic measurement offset.
        meas_mag = mag_theory + 1.0

        vr = validator.validate_with_measurements(freqs, meas_mag)
        assert vr.rms_magnitude_error_db is not None
        assert abs(vr.rms_magnitude_error_db - 1.0) < 0.01

    def test_validate_with_measurements_phase_optional(self) -> None:
        inputs = _lp_butterworth_inputs()
        result = FilterDesigner(inputs).design()
        validator = FilterValidator(result, inputs)

        freqs = np.logspace(2, 4, 30)
        mag_theory, _ = validator.theoretical_response(freqs)
        vr = validator.validate_with_measurements(freqs, mag_theory)

        assert vr.rms_phase_error_deg is None
        assert vr.measured_phase_deg is None

    def test_theoretical_response_shape_matches_input(self) -> None:
        inputs = _lp_butterworth_inputs()
        result = FilterDesigner(inputs).design()
        validator = FilterValidator(result, inputs)

        freqs = np.logspace(1, 5, 200)
        mag, phase = validator.theoretical_response(freqs)
        assert mag.shape == freqs.shape
        assert phase.shape == freqs.shape
