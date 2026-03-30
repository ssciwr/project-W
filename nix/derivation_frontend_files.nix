{
  self,
  stdenv,
  lib,
  nodejs_24,
  pnpm_10,
  pnpmConfigHook,
  fetchPnpmDeps,
  writeShellScriptBin,

  #needs to be supplied explicitly
  backend_base_url ? "",
}:

stdenv.mkDerivation (finalAttrs: {
  pname = "project_W_frontend";
  version = "0.6.0";

  src = lib.fileset.toSource {
    root = ../frontend;
    fileset = ../frontend;
  };

  nativeBuildInputs = [
    nodejs_24
    pnpm_10
    pnpmConfigHook
  ];

  buildInputs = [
    (writeShellScriptBin "git" ''
      if [ "$1" == "rev-parse" ]; then
        echo "${self.shortRev or self.dirtyShortRev}"
      else
        echo "v${finalAttrs.version}"
      fi
    '')
  ];

  pnpmDeps = fetchPnpmDeps {
    inherit (finalAttrs) pname version src;
    fetcherVersion = 2;
    hash = "sha256-lYf00JGyR6TDMjMyi1GlZW7r+IFdRU9kcGjibsPGYMQ=";
  };

  buildPhase = ''
    ${pnpm_10}/bin/pnpm build
  '';

  installPhase = ''
    cp -r build $out
  '';

  PUBLIC_BACKEND_BASE_URL = backend_base_url;

  meta = {
    description = "Frontend files for Project-W";
    homepage = "https://github.com/ssciwr/project-W";
    license = lib.licenses.agpl3Only;
  };
})
