#!/bin/bash

# Tesseract OCR Build Script
# Builds Tesseract from source with customizable version and language support

set -e  # Exit on any error

# Default values
DEFAULT_VERSION="5.5.1"
DEFAULT_LANGUAGES="eng,ara,ell"
TESSDATA_URL="https://github.com/tesseract-ocr/tessdata_best/raw/main"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m' # No Color

# Skip flags
SKIP_DEPENDENCIES=false
SKIP_LEPTONICA=false
SKIP_TESSERACT=false
SKIP_LANGUAGES=false
STATUS_ONLY=false

# Helper functions
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_status() {
    echo -e "${CYAN}[STATUS]${NC} $1"
}

print_header() {
    echo -e "${MAGENTA}=== $1 ===${NC}"
}

print_usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Options:
  -v, --version VERSION        Tesseract version to build (default: $DEFAULT_VERSION)
  -l, --languages LANGS        Comma-separated list of language codes (default: $DEFAULT_LANGUAGES)
  -p, --prefix PREFIX          Installation prefix (default: /usr/local)
  -j, --jobs JOBS             Number of parallel jobs for compilation (default: auto-detect)
  
  Skip Options:
  --skip-dependencies         Skip system dependency installation
  --skip-leptonica           Skip Leptonica build and installation
  --skip-tesseract           Skip Tesseract build and installation
  --skip-languages           Skip language data file downloads
  
  Status Options:
  --status                   Show installation status and exit
  
  -h, --help                 Show this help message

Examples:
  $0 --status                             # Check current installation status
  $0                                      # Full installation with defaults
  $0 -v 5.4.1 -l eng,fra,deu            # Build version 5.4.1 with specific languages
  $0 --skip-tesseract -l eng,spa,por     # Only download languages (Tesseract already installed)
  $0 --skip-languages                    # Install Tesseract without language files
  $0 --skip-dependencies --skip-leptonica # Only build Tesseract (dependencies already met)

Skip Scenarios:
  --skip-dependencies: Use when you've already installed system packages manually
  --skip-leptonica:    Use when Leptonica is already installed or available
  --skip-tesseract:    Use when you only want to add/update language files
  --skip-languages:    Use when you want to manage language files separately

Available language codes:
  eng (English), ara (Arabic), ell (Greek), fra (French), deu (German),
  spa (Spanish), por (Portuguese), rus (Russian), chi_sim (Chinese Simplified),
  chi_tra (Chinese Traditional), jpn (Japanese), kor (Korean), hin (Hindi),
  ita (Italian), nld (Dutch), pol (Polish), tur (Turkish), vie (Vietnamese)
  
  See full list at: https://github.com/tesseract-ocr/tessdata_best
EOF
}

# Check system dependencies
check_dependencies() {
    local missing_deps=()
    local installed_deps=()
    
    if command -v apt-get &> /dev/null; then
        # Debian/Ubuntu
        local deps=("build-essential" "cmake" "git" "pkg-config" "libtool" "autoconf" "automake" 
                    "libpng-dev" "libjpeg-dev" "libtiff-dev" "libgif-dev" "libwebp-dev" 
                    "libopenjp2-7-dev" "zlib1g-dev" "liblcms2-dev" "libicu-dev" 
                    "libpango1.0-dev" "libcairo2-dev" "curl" "wget")
        
        for dep in "${deps[@]}"; do
            if dpkg -l | grep -q "^ii  $dep "; then
                installed_deps+=("$dep")
            else
                missing_deps+=("$dep")
            fi
        done
    elif command -v yum &> /dev/null || command -v dnf &> /dev/null; then
        # RHEL/CentOS/Fedora - simplified check
        local deps=("gcc" "gcc-c++" "make" "cmake" "git" "pkgconfig" "libtool" "autoconf" "automake"
                    "libpng-devel" "libjpeg-devel" "libtiff-devel" "giflib-devel" "libwebp-devel"
                    "openjpeg2-devel" "zlib-devel" "lcms2-devel" "libicu-devel" 
                    "pango-devel" "cairo-devel" "curl" "wget")
        
        for dep in "${deps[@]}"; do
            if rpm -q "$dep" &> /dev/null; then
                installed_deps+=("$dep")
            else
                missing_deps+=("$dep")
            fi
        done
    elif command -v brew &> /dev/null; then
        # macOS with Homebrew
        local deps=("cmake" "git" "pkg-config" "libtool" "autoconf" "automake"
                    "libpng" "jpeg" "libtiff" "giflib" "webp" "openjpeg"
                    "zlib" "little-cms2" "icu4c" "pango" "cairo")
        
        for dep in "${deps[@]}"; do
            if brew list "$dep" &> /dev/null; then
                installed_deps+=("$dep")
            else
                missing_deps+=("$dep")
            fi
        done
    else
        echo "unknown"
        return
    fi
    
    echo "${#installed_deps[@]}:${#missing_deps[@]}:$(IFS=,; echo "${missing_deps[*]}")"
}

# Check Leptonica installation
check_leptonica() {
    if pkg-config --exists lept 2>/dev/null; then
        local version=$(pkg-config --modversion lept 2>/dev/null || echo "unknown")
        echo "installed:$version"
    elif ldconfig -p 2>/dev/null | grep -q leptonica; then
        echo "installed:system"
    elif [ -f "$PREFIX/lib/liblept.so" ] || [ -f "$PREFIX/lib/liblept.dylib" ]; then
        echo "installed:local"
    else
        echo "missing"
    fi
}

# Check Tesseract installation
check_tesseract() {
    if command -v tesseract &> /dev/null; then
        local version=$(tesseract --version 2>&1 | head -n1 | grep -o '[0-9]\+\.[0-9]\+\.[0-9]\+' | head -1)
        local path=$(which tesseract)
        echo "installed:$version:$path"
    else
        echo "missing"
    fi
}

# Check language files
check_languages() {
    local requested_langs="$1"
    local tessdata_dir
    
    # Find tessdata directory
    if [ -d "$PREFIX/share/tessdata" ]; then
        tessdata_dir="$PREFIX/share/tessdata"
    elif command -v tesseract &> /dev/null; then
        tessdata_dir=$(tesseract --print-parameters 2>/dev/null | grep tessdata | cut -d' ' -f2 2>/dev/null || echo "")
        if [ -z "$tessdata_dir" ]; then
            # Try common locations
            for dir in "/usr/share/tessdata" "/usr/local/share/tessdata" "/opt/homebrew/share/tessdata"; do
                if [ -d "$dir" ]; then
                    tessdata_dir="$dir"
                    break
                fi
            done
        fi
    fi
    
    if [ -z "$tessdata_dir" ] || [ ! -d "$tessdata_dir" ]; then
        echo "no_tessdata_dir"
        return
    fi
    
    local available_langs=()
    local missing_langs=()
    
    # Get available languages
    for file in "$tessdata_dir"/*.traineddata; do
        if [ -f "$file" ]; then
            local lang=$(basename "$file" .traineddata)
            available_langs+=("$lang")
        fi
    done
    
    # Check requested languages
    IFS=',' read -ra LANG_ARRAY <<< "$requested_langs"
    for lang in "${LANG_ARRAY[@]}"; do
        lang=$(echo "$lang" | xargs)  # Trim whitespace
        if [[ " ${available_langs[*]} " =~ " $lang " ]]; then
            continue  # Found
        else
            missing_langs+=("$lang")
        fi
    done
    
    echo "$tessdata_dir:$(IFS=,; echo "${available_langs[*]}"):$(IFS=,; echo "${missing_langs[*]}")"
}

# Display comprehensive status
show_status() {
    print_header "TESSERACT INSTALLATION STATUS"
    
    # System Information
    print_status "System: $(uname -s) $(uname -m)"
    print_status "Install Prefix: $PREFIX"
    echo
    
    # Check Dependencies
    print_header "SYSTEM DEPENDENCIES"
    local dep_status=$(check_dependencies)
    if [ "$dep_status" = "unknown" ]; then
        print_warning "Cannot check dependencies on this system"
    else
        IFS=':' read -ra DEP_INFO <<< "$dep_status"
        local installed_count="${DEP_INFO[0]}"
        local missing_count="${DEP_INFO[1]}"
        local missing_list="${DEP_INFO[2]}"
        
        if [ "$missing_count" -eq 0 ]; then
            print_success "All required dependencies are installed ($installed_count packages)"
        else
            print_warning "$missing_count dependencies missing, $installed_count installed"
            if [ -n "$missing_list" ]; then
                print_info "Missing: ${missing_list//,/ }"
            fi
        fi
    fi
    echo
    
    # Check Leptonica
    print_header "LEPTONICA STATUS"
    local lept_status=$(check_leptonica)
    IFS=':' read -ra LEPT_INFO <<< "$lept_status"
    case "${LEPT_INFO[0]}" in
        "installed")
            print_success "Leptonica is installed (version: ${LEPT_INFO[1]})"
            ;;
        "missing")
            print_error "Leptonica is not installed"
            ;;
    esac
    echo
    
    # Check Tesseract
    print_header "TESSERACT STATUS"
    local tess_status=$(check_tesseract)
    IFS=':' read -ra TESS_INFO <<< "$tess_status"
    case "${TESS_INFO[0]}" in
        "installed")
            print_success "Tesseract is installed"
            print_info "Version: ${TESS_INFO[1]}"
            print_info "Location: ${TESS_INFO[2]}"
            if [ "$VERSION" != "${TESS_INFO[1]}" ]; then
                print_warning "Requested version ($VERSION) differs from installed (${TESS_INFO[1]})"
            fi
            ;;
        "missing")
            print_error "Tesseract is not installed"
            ;;
    esac
    echo
    
    # Check Languages
    print_header "LANGUAGE DATA STATUS"
    local lang_status=$(check_languages "$LANGUAGES")
    IFS=':' read -ra LANG_INFO <<< "$lang_status"
    case "${LANG_INFO[0]}" in
        "no_tessdata_dir")
            print_error "Tessdata directory not found"
            ;;
        *)
            local tessdata_dir="${LANG_INFO[0]}"
            local available_langs="${LANG_INFO[1]}"
            local missing_langs="${LANG_INFO[2]}"
            
            print_info "Tessdata directory: $tessdata_dir"
            
            if [ -n "$available_langs" ]; then
                print_success "Available languages: ${available_langs//,/ }"
            else
                print_warning "No language files found"
            fi
            
            if [ -n "$missing_langs" ]; then
                print_error "Missing requested languages: ${missing_langs//,/ }"
            else
                print_success "All requested languages are available"
            fi
            ;;
    esac
    echo
    
    # Summary
    print_header "SUMMARY"
    local need_deps=false
    local need_leptonica=false
    local need_tesseract=false
    local need_languages=false
    
    # Determine what's needed
    if [ "$dep_status" != "unknown" ]; then
        IFS=':' read -ra DEP_INFO <<< "$dep_status"
        [ "${DEP_INFO[1]}" -gt 0 ] && need_deps=true
    fi
    
    [ "${LEPT_INFO[0]}" = "missing" ] && need_leptonica=true
    [ "${TESS_INFO[0]}" = "missing" ] && need_tesseract=true
    
    if [ "$lang_status" != "no_tessdata_dir" ]; then
        IFS=':' read -ra LANG_INFO <<< "$lang_status"
        [ -n "${LANG_INFO[2]}" ] && need_languages=true
    else
        need_languages=true
    fi
    
    if [ "$need_deps" = false ] && [ "$need_leptonica" = false ] && [ "$need_tesseract" = false ] && [ "$need_languages" = false ]; then
        print_success "✓ Complete Tesseract installation detected!"
        print_info "All components are installed and ready to use"
    else
        print_info "Installation requirements:"
        [ "$need_deps" = true ] && print_warning "  • System dependencies need installation"
        [ "$need_leptonica" = true ] && print_warning "  • Leptonica needs installation"
        [ "$need_tesseract" = true ] && print_warning "  • Tesseract needs installation"
        [ "$need_languages" = true ] && print_warning "  • Language files need download"
        
        echo
        print_info "Suggested command to complete installation:"
        local skip_flags=""
        [ "$need_deps" = false ] && skip_flags="$skip_flags --skip-dependencies"
        [ "$need_leptonica" = false ] && skip_flags="$skip_flags --skip-leptonica"
        [ "$need_tesseract" = false ] && skip_flags="$skip_flags --skip-tesseract"
        [ "$need_languages" = false ] && skip_flags="$skip_flags --skip-languages"
        
        echo "  $0$skip_flags"
    fi
}

# Parse command line arguments
VERSION="$DEFAULT_VERSION"
LANGUAGES="$DEFAULT_LANGUAGES"
PREFIX="/usr/local"
JOBS=$(nproc 2>/dev/null || echo 4)

while [[ $# -gt 0 ]]; do
    case $1 in
        -v|--version)
            VERSION="$2"
            shift 2
            ;;
        -l|--languages)
            LANGUAGES="$2"
            shift 2
            ;;
        -p|--prefix)
            PREFIX="$2"
            shift 2
            ;;
        -j|--jobs)
            JOBS="$2"
            shift 2
            ;;
        --skip-dependencies)
            SKIP_DEPENDENCIES=true
            shift
            ;;
        --skip-leptonica)
            SKIP_LEPTONICA=true
            shift
            ;;
        --skip-tesseract)
            SKIP_TESSERACT=true
            shift
            ;;
        --skip-languages)
            SKIP_LANGUAGES=true
            shift
            ;;
        --status)
            STATUS_ONLY=true
            shift
            ;;
        -h|--help)
            print_usage
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            print_usage
            exit 1
            ;;
    esac
done

# If status only, show status and exit
if [ "$STATUS_ONLY" = true ]; then
    show_status
    exit 0
fi

# Validate inputs
if ! [[ "$JOBS" =~ ^[0-9]+$ ]] || [ "$JOBS" -lt 1 ]; then
    print_error "Invalid number of jobs: $JOBS"
    exit 1
fi

# Show current status before starting
show_status
echo
read -p "Continue with installation? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    print_info "Installation cancelled by user"
    exit 0
fi
echo

# Show what will be executed
print_info "Build Configuration:"
if [ "$SKIP_TESSERACT" = false ]; then
    print_info "  Tesseract version: $VERSION"
fi
if [ "$SKIP_LANGUAGES" = false ]; then
    print_info "  Languages: $LANGUAGES"
fi
print_info "  Install prefix: $PREFIX"
print_info "  Parallel jobs: $JOBS"
echo
print_info "Execution Plan:"
[ "$SKIP_DEPENDENCIES" = false ] && print_info "  ✓ Install system dependencies"
[ "$SKIP_LEPTONICA" = false ] && print_info "  ✓ Build and install Leptonica"
[ "$SKIP_TESSERACT" = false ] && print_info "  ✓ Build and install Tesseract $VERSION"
[ "$SKIP_LANGUAGES" = false ] && print_info "  ✓ Download language data files"
echo

# Check for contradictory configurations
if [ "$SKIP_TESSERACT" = true ] && [ "$SKIP_LANGUAGES" = true ]; then
    print_warning "Both Tesseract and language installation are skipped. Nothing to do!"
    exit 0
fi

# Check if running as root for system-wide installation
if [[ "$PREFIX" == "/usr/local" ]] && [[ $EUID -ne 0 ]] && ([ "$SKIP_TESSERACT" = false ] || [ "$SKIP_LEPTONICA" = false ]); then
    print_warning "Installing to $PREFIX may require sudo privileges"
fi

# Detect OS and install dependencies
install_dependencies() {
    if [ "$SKIP_DEPENDENCIES" = true ]; then
        print_info "Skipping dependency installation (--skip-dependencies specified)"
        return 0
    fi
    
    print_info "Installing dependencies..."
    
    if command -v apt-get &> /dev/null; then
        # Debian/Ubuntu
        sudo apt-get update
        sudo apt-get install -y \
            build-essential \
            cmake \
            git \
            pkg-config \
            libtool \
            autoconf \
            automake \
            libpng-dev \
            libjpeg-dev \
            libtiff-dev \
            libgif-dev \
            libwebp-dev \
            libopenjp2-7-dev \
            zlib1g-dev \
            liblcms2-dev \
            libicu-dev \
            libpango1.0-dev \
            libcairo2-dev \
            curl \
            wget
    elif command -v yum &> /dev/null; then
        # RHEL/CentOS/Fedora
        sudo yum groupinstall -y "Development Tools"
        sudo yum install -y \
            cmake \
            git \
            pkgconfig \
            libtool \
            autoconf \
            automake \
            libpng-devel \
            libjpeg-devel \
            libtiff-devel \
            giflib-devel \
            libwebp-devel \
            openjpeg2-devel \
            zlib-devel \
            lcms2-devel \
            libicu-devel \
            pango-devel \
            cairo-devel \
            curl \
            wget
    elif command -v dnf &> /dev/null; then
        # Fedora (newer versions)
        sudo dnf groupinstall -y "Development Tools"
        sudo dnf install -y \
            cmake \
            git \
            pkgconfig \
            libtool \
            autoconf \
            automake \
            libpng-devel \
            libjpeg-devel \
            libtiff-devel \
            giflib-devel \
            libwebp-devel \
            openjpeg2-devel \
            zlib-devel \
            lcms2-devel \
            libicu-devel \
            pango-devel \
            cairo-devel \
            curl \
            wget
    elif command -v brew &> /dev/null; then
        # macOS with Homebrew
        brew install \
            cmake \
            git \
            pkg-config \
            libtool \
            autoconf \
            automake \
            libpng \
            jpeg \
            libtiff \
            giflib \
            webp \
            openjpeg \
            zlib \
            little-cms2 \
            icu4c \
            pango \
            cairo
    else
        print_error "Unsupported package manager. Please install dependencies manually."
        print_info "Required packages: build tools, cmake, git, image libraries (png, jpeg, tiff, webp, etc.)"
        exit 1
    fi
}

# Download and build leptonica (required dependency)
build_leptonica() {
    if [ "$SKIP_LEPTONICA" = true ]; then
        print_info "Skipping Leptonica build (--skip-leptonica specified)"
        return 0
    fi
    
    print_info "Building Leptonica..."
    
    cd /tmp
    if [ -d "leptonica" ]; then
        rm -rf leptonica
    fi
    
    git clone https://github.com/DanBloomberg/leptonica.git
    cd leptonica
    
    ./autogen.sh
    ./configure --prefix="$PREFIX"
    make -j"$JOBS"
    
    if [[ "$PREFIX" == "/usr/local" ]] && [[ $EUID -ne 0 ]]; then
        sudo make install
    else
        make install
    fi
    
    # Update library cache
    if command -v ldconfig &> /dev/null; then
        if [[ $EUID -eq 0 ]]; then
            ldconfig
        else
            sudo ldconfig 2>/dev/null || true
        fi
    fi
}

# Download and build Tesseract
build_tesseract() {
    if [ "$SKIP_TESSERACT" = true ]; then
        print_info "Skipping Tesseract build (--skip-tesseract specified)"
        return 0
    fi
    
    print_info "Downloading Tesseract $VERSION..."
    
    cd /tmp
    if [ -d "tesseract-$VERSION" ]; then
        rm -rf "tesseract-$VERSION"
    fi
    
    wget "https://github.com/tesseract-ocr/tesseract/archive/refs/tags/$VERSION.tar.gz" -O "tesseract-$VERSION.tar.gz"
    tar -xzf "tesseract-$VERSION.tar.gz"
    cd "tesseract-$VERSION"
    
    print_info "Configuring Tesseract build..."
    ./autogen.sh
    
    # Configure with proper library paths
    export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig:$PKG_CONFIG_PATH"
    export LD_LIBRARY_PATH="$PREFIX/lib:$LD_LIBRARY_PATH"
    
    ./configure --prefix="$PREFIX" \
                --with-extra-includes="$PREFIX/include" \
                --with-extra-libraries="$PREFIX/lib"
    
    print_info "Compiling Tesseract (this may take a while)..."
    make -j"$JOBS"
    
    print_info "Installing Tesseract..."
    if [[ "$PREFIX" == "/usr/local" ]] && [[ $EUID -ne 0 ]]; then
        sudo make install
    else
        make install
    fi
    
    # Update library cache
    if command -v ldconfig &> /dev/null; then
        if [[ $EUID -eq 0 ]]; then
            ldconfig
        else
            sudo ldconfig 2>/dev/null || true
        fi
    fi
}

# Download language data files
download_tessdata() {
    if [ "$SKIP_LANGUAGES" = true ]; then
        print_info "Skipping language data download (--skip-languages specified)"
        return 0
    fi
    
    print_info "Downloading language data files..."
    
    TESSDATA_DIR="$PREFIX/share/tessdata"
    
    # Create tessdata directory
    if [[ "$PREFIX" == "/usr/local" ]] && [[ $EUID -ne 0 ]]; then
        sudo mkdir -p "$TESSDATA_DIR"
    else
        mkdir -p "$TESSDATA_DIR"
    fi
    
    # Split languages by comma and download each
    IFS=',' read -ra LANG_ARRAY <<< "$LANGUAGES"
    for lang in "${LANG_ARRAY[@]}"; do
        lang=$(echo "$lang" | xargs)  # Trim whitespace
        print_info "Downloading $lang.traineddata..."
        
        if [[ "$PREFIX" == "/usr/local" ]] && [[ $EUID -ne 0 ]]; then
            sudo wget "$TESSDATA_URL/$lang.traineddata" -O "$TESSDATA_DIR/$lang.traineddata" || {
                print_warning "Failed to download $lang.traineddata"
            }
        else
            wget "$TESSDATA_URL/$lang.traineddata" -O "$TESSDATA_DIR/$lang.traineddata" || {
                print_warning "Failed to download $lang.traineddata"
            }
        fi
    done
}

# Verify installation
verify_installation() {
    print_info "Verifying installation..."
    
    # Only verify Tesseract if it was supposed to be installed
    if [ "$SKIP_TESSERACT" = false ]; then
        # Check if tesseract binary exists and is executable
        if command -v tesseract &> /dev/null; then
            TESSERACT_VERSION=$(tesseract --version 2>&1 | head -n1)
            print_success "Tesseract installed successfully!"
            print_info "Version: $TESSERACT_VERSION"
        else
            print_error "Tesseract installation failed or not in PATH"
            print_info "You may need to add $PREFIX/bin to your PATH"
            exit 1
        fi
    fi
    
    # Verify languages if they were supposed to be installed
    if [ "$SKIP_LANGUAGES" = false ] && command -v tesseract &> /dev/null; then
        print_info "Available languages:"
        tesseract --list-langs 2>/dev/null || print_warning "Could not list languages"
    elif [ "$SKIP_LANGUAGES" = false ]; then
        print_warning "Cannot verify languages - Tesseract not found in PATH"
    fi
    
    # Show what was actually done
    echo
    print_success "Build process completed!"
    if [ "$SKIP_DEPENDENCIES" = false ]; then
        print_info "✓ Dependencies installed"
    fi
    if [ "$SKIP_LEPTONICA" = false ]; then
        print_info "✓ Leptonica built and installed"
    fi
    if [ "$SKIP_TESSERACT" = false ]; then
        print_info "✓ Tesseract $VERSION built and installed"
    fi
    if [ "$SKIP_LANGUAGES" = false ]; then
        print_info "✓ Language files downloaded: $LANGUAGES"
    fi
}

# Main execution
main() {
    print_info "Starting Tesseract OCR build process..."
    
    # Create temporary directory for build
    mkdir -p /tmp/tesseract-build
    
    install_dependencies
    build_leptonica
    build_tesseract
    download_tessdata
    verify_installation
    
    # Cleanup
    if [ "$SKIP_TESSERACT" = false ] || [ "$SKIP_LEPTONICA" = false ]; then
        print_info "Cleaning up temporary files..."
        cd /tmp
        rm -rf leptonica tesseract-* tesseract-build
    fi
    
    print_success "Process completed successfully!"
    print_info "Installation location: $PREFIX"
    if command -v tesseract &> /dev/null; then
        print_info "To use Tesseract, run: tesseract input.png output.txt"
    fi
    
    # Show final status
    echo
    print_header "FINAL STATUS"
    show_status
}

# Run main function
main "$@"