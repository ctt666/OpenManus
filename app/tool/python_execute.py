import multiprocessing
import sys
from io import StringIO
from typing import Dict, Optional

from app.logger import logger
from app.sandbox.core.manager import SandboxManager
from app.tool.base import BaseTool


class PythonExecute(BaseTool):
    """A tool for executing Python code with timeout and safety restrictions."""

    name: str = "python_execute"
    description: str = "Executes Python code string. Note: Only print outputs are visible, function return values are not captured. Use print statements to see results."
    parameters: dict = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "The Python code to execute.",
            },
        },
        "required": ["code"],
    }

    def _run_code(self, code: str, result_dict: dict, safe_globals: dict) -> None:
        original_stdout = sys.stdout
        try:
            output_buffer = StringIO()
            sys.stdout = output_buffer
            exec(code, safe_globals, safe_globals)
            result_dict["observation"] = output_buffer.getvalue()
            result_dict["success"] = True
        except Exception as e:
            result_dict["observation"] = str(e)
            result_dict["success"] = False
        finally:
            sys.stdout = original_stdout

    async def execute(
        self,
        code: str,
        timeout: int = 5,
        sandbox_id: Optional[str] = None,
        sandbox_manager: Optional[SandboxManager] = None,
    ) -> Dict:
        """
        Executes the provided Python code with a timeout.

        Args:
            code (str): The Python code to execute.
            timeout (int): Execution timeout in seconds.
            sandbox_id (Optional[str]): Optional sandbox ID to execute in Docker sandbox.
                If provided, code will be executed in the sandbox. If None, uses local execution.
            sandbox_manager (Optional[SandboxManager]): Optional SandboxManager instance.
                If None, uses global singleton instance.

        Returns:
            Dict: Contains 'output' with execution output or error message and 'success' status.
        """
        # If sandbox_id is provided, execute in Docker sandbox
        if sandbox_id:
            return await self._execute_in_sandbox(
                code, timeout, sandbox_id, sandbox_manager
            )

        # Otherwise, use local execution (backward compatible)
        return await self._execute_locally(code, timeout)

    async def _execute_in_sandbox(
        self,
        code: str,
        timeout: int,
        sandbox_id: str,
        sandbox_manager: Optional[SandboxManager],
    ) -> Dict:
        """Execute Python code in Docker sandbox.

        Note: stderr is automatically captured by sandbox.run_command through socket.
        """
        manager = sandbox_manager or SandboxManager()

        try:
            # Get sandbox instance
            async with manager.sandbox_operation(sandbox_id) as sandbox:
                # Write code to temporary file in sandbox
                # Use unique filename to avoid conflicts
                import uuid
                script_path = f"/tmp/script_{uuid.uuid4().hex[:8]}.py"

                try:
                    await sandbox.write_file(script_path, code)

                    # Execute the script with stderr redirection to ensure all output is captured
                    # Note: sandbox.run_command already captures all output via socket,
                    # but 2>&1 ensures stderr is included in the output stream
                    try:
                        exit_code_marker = "__OPENMANUS_PY_EXIT_CODE__="
                        output = await sandbox.run_command(
                            f"python3 {script_path} 2>&1; printf '\\n{exit_code_marker}%s\\n' $?",
                            timeout=timeout,
                        )
                        # Determine success based on process exit code (sandbox.run_command does not
                        # raise on non-zero exit code; it returns output only).
                        exit_code: Optional[int] = None
                        cleaned_lines: list[str] = []
                        for line in output.splitlines():
                            if line.startswith(exit_code_marker):
                                try:
                                    exit_code = int(line[len(exit_code_marker) :].strip())
                                except ValueError:
                                    exit_code = None
                                continue
                            cleaned_lines.append(line)
                        cleaned_output = "\n".join(cleaned_lines).strip()
                        return {
                            "observation": cleaned_output,
                            "success": (exit_code == 0) if exit_code is not None else True,
                        }
                    except Exception as e:
                        # Capture error output (includes both stdout and stderr)
                        error_msg = str(e)
                        # Try to get more detailed error by checking if file exists and reading it
                        try:
                            # Check if there's any output from the failed execution
                            check_output = await sandbox.run_command(
                                f"python3 {script_path} 2>&1 || true", timeout=2
                            )
                            if check_output:
                                error_msg = check_output
                        except:
                            pass  # If we can't get more details, use the exception message

                        return {
                            "observation": f"Error executing code in sandbox: {error_msg}",
                            "success": False,
                        }
                    finally:
                        # Clean up temporary file
                        try:
                            await sandbox.run_command(f"rm -f {script_path}", timeout=2)
                        except Exception as cleanup_error:
                            logger.warning(
                                f"Failed to clean up temporary file {script_path} in sandbox {sandbox_id}: {cleanup_error}"
                            )
                except Exception as e:
                    return {
                        "observation": f"Error preparing code in sandbox: {str(e)}",
                        "success": False,
                    }
        except KeyError:
            return {
                "observation": f"Sandbox {sandbox_id} not found",
                "success": False,
            }
        except Exception as e:
            return {
                "observation": f"Error accessing sandbox: {str(e)}",
                "success": False,
            }

    async def _execute_locally(self, code: str, timeout: int) -> Dict:
        """Execute Python code locally (backward compatible)."""
        with multiprocessing.Manager() as manager:
            result = manager.dict({"observation": "", "success": False})
            if isinstance(__builtins__, dict):
                safe_globals = {"__builtins__": __builtins__}
            else:
                safe_globals = {"__builtins__": __builtins__.__dict__.copy()}
            proc = multiprocessing.Process(
                target=self._run_code, args=(code, result, safe_globals)
            )
            proc.start()
            proc.join(timeout)

            # timeout process
            if proc.is_alive():
                proc.terminate()
                proc.join(1)
                return {
                    "observation": f"Execution timeout after {timeout} seconds",
                    "success": False,
                }
            return dict(result)
