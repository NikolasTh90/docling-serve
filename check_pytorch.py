import os
import sys
import platform
import subprocess
import torch

def run_cmd(cmd):
    try:
        completed = subprocess.run(cmd, shell=False, check=True,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT,
                                   text=True)
        return completed.stdout.strip()
    except Exception as e:
        return f"Error running {cmd}: {e}"

def print_header(title):
    print("\n" + "=" * 10 + f" {title} " + "=" * 10)

def main():
    # 1) System / Python
    print_header("SYSTEM")
    print(f"OS:            {platform.system()} {platform.release()} ({platform.version()})")
    print(f"Architecture:  {platform.machine()}")
    print(f"Python:        {platform.python_implementation()} {platform.python_version()}")
    print(f"Executable:    {sys.executable}")

    # 2) Environment variables
    print_header("ENVIRONMENT VARIABLES")
    for var in ("CUDA_HOME",
                "CUDA_PATH",
                "CUDA_VISIBLE_DEVICES",
                "NVIDIA_VISIBLE_DEVICES",
                "LD_LIBRARY_PATH",
                "PATH"):
        print(f"{var:20s}: {os.environ.get(var)}")

    # 3) nvidia-smi
    print_header("nvidia-smi")
    print(run_cmd(["nvidia-smi", "--query-gpu=name,index,driver_version,memory.total,utilization.gpu",
                   "--format=csv,noheader"]))

    # 4) PyTorch + CUDA
    print_header("PYTORCH / CUDA")
    print(f"torch.__version__:         {torch.__version__}")
    print(f"Built with CUDA support:   {torch.version.cuda is not None}")
    print(f"torch.version.cuda:        {torch.version.cuda}")
    print(f"cuDNN version:             {torch.backends.cudnn.version()}")
    print(f"cuDNN enabled:             {torch.backends.cudnn.enabled}")
    print(f"CUDA available:            {torch.cuda.is_available()}")
    print(f"CUDA devices count:        {torch.cuda.device_count()}")

    # 5) Per‐device info
    if torch.cuda.is_available():
        for i in range(torch.cuda.device_count()):
            props = torch.cuda.get_device_properties(i)
            print_header(f"DEVICE {i}")
            print(f"Name:            {props.name}")
            print(f"Compute capability: {props.major}.{props.minor}")
            print(f"Total memory:      {props.total_memory/1024**3:.2f} GB")
            print(f"Multi-processor count: {props.multi_processor_count}")
            print(f"Max threads per block: {props.max_threads_per_block}")
            print(f"Max grid dims:        {props.max_grid_size}")
            # current device
            print(f"Current device index: {torch.cuda.current_device()}")
            # memory stats
            torch.cuda.reset_peak_memory_stats(i)
            _ = torch.empty(1024, device=f"cuda:{i}")  # allocate small tensor
            print(f"Reserved memory:    {torch.cuda.memory_reserved(i)/1024**2:.2f} MB")
            print(f"Allocated memory:   {torch.cuda.memory_allocated(i)/1024**2:.2f} MB")

    else:
        print("No CUDA devices detected by torch.cuda.")

    # 6) Simple allocation test
    print_header("SIMPLE ALLOCATION TEST")
    try:
        if torch.cuda.is_available():
            x = torch.randn(3, 3, device="cuda")
            print(" -> Successfully allocated a tensor on GPU:", x.device)
        else:
            print(" -> Skipping: CUDA not available.")
    except Exception as e:
        print(" -> Allocation failed:", e)

if __name__ == "__main__":
    main()