"""Tests for the shared handler_utils module.

Covers:
- HandlerError construction and attributes
- check_params validation logic
- server.py dispatch catches HandlerError with proper error code
"""

from __future__ import annotations

import pytest

from minimax_code.ipc.handler_utils import HandlerError, check_params
from minimax_code.ipc.protocol import INVALID_PARAMS

# ---------------------------------------------------------------------------
# HandlerError
# ---------------------------------------------------------------------------

class TestHandlerError:
    def test_attributes(self):
        err = HandlerError(-32602, "bad params", data={"key": "val"})
        assert err.code == -32602
        assert err.message == "bad params"
        assert err.data == {"key": "val"}

    def test_data_defaults_to_none(self):
        err = HandlerError(-32603, "oops")
        assert err.data is None

    def test_is_exception(self):
        err = HandlerError(-32602, "test")
        assert isinstance(err, Exception)
        with pytest.raises(HandlerError):
            raise err


# ---------------------------------------------------------------------------
# check_params
# ---------------------------------------------------------------------------

class TestCheckParams:
    def test_passes_when_all_keys_present(self):
        check_params({"a": 1, "b": 2}, expected_keys={"a", "b"})

    def test_passes_with_extra_keys(self):
        check_params({"a": 1, "b": 2, "c": 3}, expected_keys={"a"})

    def test_raises_on_none_params(self):
        with pytest.raises(HandlerError) as exc_info:
            check_params(None, expected_keys={"id"})
        assert exc_info.value.code == INVALID_PARAMS
        assert "JSON object" in exc_info.value.message

    def test_raises_on_non_dict_params(self):
        with pytest.raises(HandlerError) as exc_info:
            check_params("not a dict", expected_keys={"id"})
        assert exc_info.value.code == INVALID_PARAMS

    def test_raises_on_missing_keys(self):
        with pytest.raises(HandlerError) as exc_info:
            check_params({"a": 1}, expected_keys={"a", "b"})
        assert exc_info.value.code == INVALID_PARAMS
        assert "b" in exc_info.value.message  # missing key listed

    def test_noop_on_empty_expected_keys(self):
        check_params(None, expected_keys=set())
        check_params("anything", expected_keys=set())

    def test_raises_on_empty_dict_when_keys_required(self):
        with pytest.raises(HandlerError) as exc_info:
            check_params({}, expected_keys={"id"})
        assert exc_info.value.code == INVALID_PARAMS


# ---------------------------------------------------------------------------
# Integration: server dispatch catches HandlerError
# ---------------------------------------------------------------------------

class TestServerDispatchCatchesHandlerError:
    """Verify that the IPCServer dispatch layer catches HandlerError
    and converts it into a proper JSON-RPC error response."""

    @pytest.mark.asyncio
    async def test_handle_request_catches_handler_error(self):
        from unittest.mock import AsyncMock

        from minimax_code.ipc.protocol import INVALID_PARAMS
        from minimax_code.ipc.server import IPCServer

        server = IPCServer.__new__(IPCServer)
        server._handlers = {}
        server._notification_handlers = {}
        server._write_lock = AsyncMock()
        server._stop = AsyncMock()
        server.listeners = []

        async def bad_handler(params, ctx):
            raise HandlerError(INVALID_PARAMS, "test error", data={"detail": "x"})

        server.register("test.bad", bad_handler)

        obj = {"jsonrpc": "2.0", "id": 1, "method": "test.bad", "params": {}}
        result = await server.handle_request(obj)

        assert result is not None
        assert result["error"]["code"] == INVALID_PARAMS
        assert "test error" in result["error"]["message"]
        assert result["error"]["data"] == {"detail": "x"}

    @pytest.mark.asyncio
    async def test_handle_request_catches_generic_exception(self):
        from unittest.mock import AsyncMock

        from minimax_code.ipc.protocol import INTERNAL_ERROR
        from minimax_code.ipc.server import IPCServer

        server = IPCServer.__new__(IPCServer)
        server._handlers = {}
        server._notification_handlers = {}
        server._write_lock = AsyncMock()
        server._stop = AsyncMock()
        server.listeners = []

        async def crash_handler(params, ctx):
            raise RuntimeError("boom")

        server.register("test.crash", crash_handler)

        obj = {"jsonrpc": "2.0", "id": 2, "method": "test.crash", "params": {}}
        result = await server.handle_request(obj)

        assert result is not None
        assert result["error"]["code"] == INTERNAL_ERROR
        assert "internal error" in result["error"]["message"]


# ---------------------------------------------------------------------------
# Import verification: all handlers use shared module
# ---------------------------------------------------------------------------

class TestNoDuplicateDefinitions:
    """Verify that no handler file re-defines _HandlerError or _check_params."""

    def test_no_duplicate_handler_error(self):
        import os
        ipc_dir = os.path.dirname(__file__).replace("tests", "minimax_code/ipc")
        for fname in os.listdir(ipc_dir):
            if not fname.startswith("handlers_") or not fname.endswith(".py"):
                continue
            fpath = os.path.join(ipc_dir, fname)
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            assert "class _HandlerError" not in content, f"{fname} still has _HandlerError"
            assert "class _ParamError" not in content, f"{fname} still has _ParamError"

    def test_no_duplicate_check_params(self):
        import os
        ipc_dir = os.path.dirname(__file__).replace("tests", "minimax_code/ipc")
        for fname in os.listdir(ipc_dir):
            if not fname.startswith("handlers_") or not fname.endswith(".py"):
                continue
            fpath = os.path.join(ipc_dir, fname)
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            assert "def _check_params" not in content, f"{fname} still has _check_params"

    def test_all_handlers_import_from_shared(self):
        import os
        ipc_dir = os.path.dirname(__file__).replace("tests", "minimax_code/ipc")
        for fname in os.listdir(ipc_dir):
            if not fname.startswith("handlers_") or not fname.endswith(".py"):
                continue
            fpath = os.path.join(ipc_dir, fname)
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            # Must use shared imports if it references HandlerError or check_params
            has_usage = "HandlerError" in content or "check_params(" in content
            has_import = "from .handler_utils import" in content
            if has_usage:
                assert has_import, f"{fname} uses HandlerError/check_params but doesn't import from handler_utils"
