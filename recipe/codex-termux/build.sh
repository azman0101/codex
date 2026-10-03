TERMUX_PKG_HOMEPAGE=https://github.com/openai/codex
TERMUX_PKG_DESCRIPTION="Codex Android - local native build without the TUR repository"
TERMUX_PKG_LICENSE="Apache-2.0, MIT"
TERMUX_PKG_LICENSE_FILE="../LICENSE, ../NOTICE"
TERMUX_PKG_MAINTAINER="Local Codex Android build"
TERMUX_PKG_VERSION="0.160.0"
TERMUX_PKG_REVISION=1
TERMUX_PKG_SRCURL="https://github.com/openai/codex/archive/refs/tags/rust-v$TERMUX_PKG_VERSION.tar.gz"
# build-linux.sh remplace cette valeur dans sa copie après contrôle des sources.
TERMUX_PKG_SHA256=0000000000000000000000000000000000000000000000000000000000000000
TERMUX_PKG_DEPENDS="libc++, openssl"
TERMUX_PKG_BUILD_IN_SRC=true
# rusty-v8 doesn't support them
TERMUX_PKG_EXCLUDED_ARCHES="arm, i686"
TERMUX_PKG_AUTO_UPDATE=false
TERMUX_PKG_UPDATE_VERSION_SED_REGEXP='s/rust-v//'
TERMUX_PKG_ON_DEVICE_BUILD_NOT_SUPPORTED=true

codex_setup_rust() {
	termux_setup_rust
	: "${CODEX_RUST_TOOLCHAIN:?Run build-linux.sh to select a dated toolchain}"
	rustup toolchain install "$CODEX_RUST_TOOLCHAIN" --profile minimal \
		--component rust-src --target "$CARGO_TARGET_NAME"
	export RUSTUP_TOOLCHAIN="$CODEX_RUST_TOOLCHAIN"
}

codex_python() {
	"$CODEX_SUPPORT_DIR/bin/uv" run --offline --no-project --python /usr/bin/python3 \
		"$CODEX_SUPPORT_DIR/tools.py" "$@"
}

codex_prepare_lockfile() {
	local phase="$1"
	local records="$(dirname "$CODEX_BUILD_METADATA")/lockfiles-$TERMUX_ARCH"
	mkdir -p "$records"
	cp Cargo.lock "$records/$phase-before.lock"
	# La release peut nécessiter une normalisation initiale. Cargo préserve les
	# versions compatibles déjà verrouillées ; consigner et contrôler le résultat.
	cargo +"$CODEX_RUST_TOOLCHAIN" metadata --format-version 1 >/dev/null
	codex_python check-lock "$records/$phase-before.lock" Cargo.lock "$records/$phase-changes.json"
	cp Cargo.lock "$records/$phase-after.lock"
}

termux_step_pre_configure() {
	codex_setup_rust

	cd codex-rs

	: "${CARGO_HOME:=$HOME/.cargo}"
	export CARGO_HOME

	codex_prepare_lockfile codex-upstream
	cargo +"$CODEX_RUST_TOOLCHAIN" vendor --locked
	find ./vendor \
		-mindepth 1 -maxdepth 1 -type d \
		! -wholename ./vendor/cc \
		! -wholename ./vendor/v8 \
		-exec rm -rf '{}' \;

	patch --batch --fuzz=0 -p1 \
		-d ./vendor/cc/ \
		< "$TERMUX_PKG_BUILDER_DIR"/rust-cc-do-not-concatenate-all-the-CFLAGS.diff

	patch --batch --fuzz=0 -p1 \
		-d ./vendor/cc/ \
		< "$TERMUX_PKG_BUILDER_DIR"/rust-cc-allow-warnings.diff

	patch --batch --fuzz=0 -p1 \
		-d ./vendor/v8/ \
		< "$TERMUX_PKG_BUILDER_DIR"/rusty-v8-search-files-with-target-suffix.diff

	grep -q '^\[patch.crates-io\]$' Cargo.toml || \
		termux_error_exit "Expected [patch.crates-io] in the inspected Codex sources"
	sed -i '/\[patch.crates-io\]/a cc = { path = "./vendor/cc" }' Cargo.toml
	sed -i '/\[patch.crates-io\]/a v8 = { path = "./vendor/v8" }' Cargo.toml
	# Enregistrer les deux sources locales avec les mêmes contrôles de versions.
	codex_prepare_lockfile codex-local-patches
}

__fetch_rusty_v8() {
	pushd "$TERMUX_PKG_SRCDIR"
	local v8_version
	v8_version="$(codex_python v8-version Cargo.lock)"
	if [ ! -d "$TERMUX_PKG_SRCDIR"/librusty_v8 ]; then
		rm -rf "$TERMUX_PKG_SRCDIR"/librusty_v8-tmp
		git init librusty_v8-tmp
		cd librusty_v8-tmp
		git remote add origin https://github.com/denoland/rusty_v8.git
		git fetch --depth=1 origin v"$v8_version"
		git reset --hard FETCH_HEAD
		git submodule update --init --recursive --depth=1
		local f
		for f in "$TERMUX_PKG_BUILDER_DIR"/v8-patches/*.patch; do
			echo "Applying patch: $(basename "$f")"
			patch --batch --fuzz=0 -p1 < "$f"
		done
		mv "$TERMUX_PKG_SRCDIR"/librusty_v8-tmp "$TERMUX_PKG_SRCDIR"/librusty_v8
	fi
	popd # "$TERMUX_PKG_SRCDIR"
}

__build_rusty_v8() {
	local __SRC_DIR="$TERMUX_PKG_SRCDIR"/librusty_v8
	pushd "$__SRC_DIR"

	codex_prepare_lockfile rusty-v8
	termux_setup_ninja
	termux_setup_gn

	export EXTRA_GN_ARGS="
android_ndk_api_level=$TERMUX_PKG_API_LEVEL
android_ndk_root=\"$NDK\"
android_ndk_version=\"$TERMUX_NDK_VERSION\"
"

	if [ "$TERMUX_ARCH" = "arm" ]; then
		EXTRA_GN_ARGS+=" target_cpu = \"arm\""
		EXTRA_GN_ARGS+=" v8_target_cpu = \"arm\""
		EXTRA_GN_ARGS+=" arm_arch = \"armv7-a\""
		EXTRA_GN_ARGS+=" arm_float_abi = \"softfp\""
	fi

	# shellcheck disable=SC2155 # Ignore command exit-code
	export GN="$(command -v gn)"

	# Make build.rs happy
	ln -sf "$NDK" "$__SRC_DIR"/third_party/android_ndk

	BINDGEN_EXTRA_CLANG_ARGS="--target=$CCTERMUX_HOST_PLATFORM"
	BINDGEN_EXTRA_CLANG_ARGS+=" --sysroot=$__SRC_DIR/third_party/android_ndk/toolchains/llvm/prebuilt/linux-x86_64/sysroot"
	export BINDGEN_EXTRA_CLANG_ARGS
	local env_name=BINDGEN_EXTRA_CLANG_ARGS_${CARGO_TARGET_NAME@U}
	env_name=${env_name//-/_}
	export "$env_name"="$BINDGEN_EXTRA_CLANG_ARGS"

	export V8_FROM_SOURCE=1
	# TODO: How to track the output of v8's build.rs without passing `-vv`
	cargo +"$CODEX_RUST_TOOLCHAIN" build --locked \
		--features v8_enable_sandbox \
		--jobs "${TERMUX_PKG_MAKE_PROCESSES}" --target "${CARGO_TARGET_NAME}" --release

	unset BINDGEN_EXTRA_CLANG_ARGS "$env_name" V8_FROM_SOURCE
	unset EXTRA_GN_ARGS

	popd # "$__SRC_DIR"
}

__install_rusty_v8() {
	local __SRC_DIR="$TERMUX_PKG_SRCDIR"/librusty_v8
	local _prefix="${TERMUX_PKG_TMPDIR}/rusty_v8_prefix"
	mkdir -p "${_prefix}"
	install -Dm600 -t "${_prefix}/include/librusty_v8" "$__SRC_DIR/target/${CARGO_TARGET_NAME}/release/gn_out/src_binding.rs"
	install -Dm600 -t "${_prefix}/lib" "$__SRC_DIR/target/${CARGO_TARGET_NAME}/release/gn_out/obj/librusty_v8.a"
}

termux_step_configure() {
	TERMUX_PKG_SRCDIR="${TERMUX_PKG_SRCDIR%/codex-rs}/codex-rs"
	TERMUX_PKG_BUILDDIR="${TERMUX_PKG_BUILDDIR%/codex-rs}/codex-rs"
	codex_setup_rust

	# Fetch librusty-v8
	__fetch_rusty_v8
	# Build librusty-v8
	__build_rusty_v8
	# Install librusty-v8
	__install_rusty_v8
}

termux_step_make() {
	codex_setup_rust

	local env_name=${CARGO_TARGET_NAME@U}
	env_name=${env_name//-/_}
	export RUSTY_V8_ARCHIVE_${env_name}="${TERMUX_PKG_TMPDIR}/rusty_v8_prefix/lib/librusty_v8.a"
	export RUSTY_V8_SRC_BINDING_PATH_${env_name}="${TERMUX_PKG_TMPDIR}/rusty_v8_prefix/include/librusty_v8/src_binding.rs"

	# ld.lld: error: undefined symbol: __clear_cache
	if [[ "${TERMUX_ARCH}" == "aarch64" ]]; then
		export CARGO_TARGET_${env_name}_RUSTFLAGS+=" -C link-arg=$($CC -print-libgcc-file-name)"
	fi

	local _release_opt="--release"
	if [ "$TERMUX_DEBUG_BUILD" = "true" ]; then
		_release_opt=
	fi

	# FIXME: Figure out why CARGO_TARGET_${env_name}_RUSTFLAGS is ignored
	local _extra_args_var_name="CARGO_TARGET_${env_name}_RUSTFLAGS"
	local _extra_args="${!_extra_args_var_name}"
	cargo +"$CODEX_RUST_TOOLCHAIN" rustc --locked \
		-p codex-cli \
		--bin codex \
		${_release_opt} \
		--jobs $TERMUX_PKG_MAKE_PROCESSES \
		--target $CARGO_TARGET_NAME \
		-- ${_extra_args}
	# Le moteur Code Mode de 0.160.0 s’exécute dans ce binaire compagnon.
	cargo +"$CODEX_RUST_TOOLCHAIN" rustc --locked \
		-p codex-code-mode-host --bin codex-code-mode-host \
		${_release_opt} --jobs "$TERMUX_PKG_MAKE_PROCESSES" \
		--target "$CARGO_TARGET_NAME" -- ${_extra_args}
	TERMUX_ARCH="$TERMUX_ARCH" CARGO_TARGET_NAME="$CARGO_TARGET_NAME" \
		TERMUX_PKG_API_LEVEL="$TERMUX_PKG_API_LEVEL" TERMUX_NDK_VERSION="$TERMUX_NDK_VERSION" \
		TERMUX_PKG_BUILDER_DIR="$TERMUX_PKG_BUILDER_DIR" \
		codex_python metadata "$CODEX_BUILD_METADATA" Cargo.lock
}

termux_step_make_install() {
	local _folder="release"
	if [ "$TERMUX_DEBUG_BUILD" = "true" ]; then
		_folder="debug"
	fi

	install -Dm700 -t $TERMUX_PREFIX/bin target/${CARGO_TARGET_NAME}/${_folder}/codex
	install -Dm700 -t "$TERMUX_PREFIX/bin" "target/${CARGO_TARGET_NAME}/${_folder}/codex-code-mode-host"

	rm -rf $TERMUX_PREFIX/share/doc/$TERMUX_PKG_NAME
	mkdir -p $TERMUX_PREFIX/share/doc/$TERMUX_PKG_NAME
	cp -Rfv ../docs/* $TERMUX_PREFIX/share/doc/$TERMUX_PKG_NAME/
	# Chemin explicite pour l’archive autonome, indépendant de l’emplacement
	# des licences choisi par la version du framework Termux.
	local license_dir="$TERMUX_PREFIX/share/$TERMUX_PKG_NAME"
	mkdir -p "$license_dir"
	install -m644 ../LICENSE ../NOTICE "$license_dir/"
	local project license
	for project in librusty_v8 librusty_v8/v8; do
		for license in "$TERMUX_PKG_SRCDIR/$project"/LICENSE*; do
			[[ -f "$license" ]] || continue
			install -m644 "$license" "$license_dir/${project//\//-}-$(basename "$license")"
		done
	done
}
