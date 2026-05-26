class StoaAgent < Formula
  include Language::Python::Virtualenv

  desc "STOA Agent — a chamber of six sovereign LLMs + one dispatcher (CLI)"
  homepage "https://stoax.xyz"
  # Stable source: PyPI sdist (more reliable + cacheable than GitHub Release
  # asset; trusted-publisher OIDC chain guarantees authenticity).
  url "https://files.pythonhosted.org/packages/a6/e2/7f0c14a3793e3016ce696e1d0d792803ed8ea8b0d14de026057a494d3194/stoa_agent-0.14.3.tar.gz"
  sha256 "a3924e0994e85608563d4320cb8cacfc59eba4e3ff984185bd6cdf843f324480"
  license "MIT"

  depends_on "certifi" => :no_linkage
  depends_on "cryptography" => :no_linkage
  depends_on "libyaml"
  depends_on "python@3.14"

  pypi_packages ignore_packages: %w[certifi cryptography pydantic]

  # Refresh resource stanzas after bumping the source url/version:
  #   brew update-python-resources --print-only stoa-agent

  def install
    venv = virtualenv_create(libexec, "python3.14")
    venv.pip_install resources
    venv.pip_install buildpath

    pkgshare.install "skills", "optional-skills"

    # Audit v13 HIGH-1 fix: shim names + env vars match the runtime
    # binaries declared in pyproject.toml ([project.scripts]:
    # stoa / stoa-agent / stoa-acp). The previous formula iterated
    # hermes/hermes-agent/hermes-acp + set HERMES_* env vars which
    # the post-rebrand CLI doesn't read — the formula tested green
    # but produced unusable shims.
    %w[stoa stoa-agent stoa-acp].each do |exe|
      next unless (libexec/"bin"/exe).exist?

      (bin/exe).write_env_script(
        libexec/"bin"/exe,
        STOA_BUNDLED_SKILLS: pkgshare/"skills",
        STOA_OPTIONAL_SKILLS: pkgshare/"optional-skills",
        STOA_MANAGED: "homebrew"
      )
    end
  end

  test do
    assert_match "STOA Agent v#{version}", shell_output("#{bin}/stoa version")

    managed = shell_output("#{bin}/stoa update 2>&1")
    assert_match "managed by Homebrew", managed
    assert_match "brew upgrade stoa-agent", managed
  end
end
