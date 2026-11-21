class Unienv < Formula
  desc "One always-ready environment shared by every AI tool"
  homepage "https://github.com/rupeshbharambe24/unienv-ai"
  url "https://github.com/rupeshbharambe24/unienv-ai/archive/refs/tags/v0.2.2.tar.gz"
  sha256 "REPLACE_WITH_RELEASE_SHA256"
  license "MIT"

  depends_on "python@3.12"
  depends_on "uv"

  def install
    libexec.install "src", "recipes", "templates", "shims"
    (bin/"unienv").write <<~EOS
      #!/bin/sh
      exec "#{Formula["python@3.12"].opt_bin}/python3.12" "#{libexec}/src/unienv.py" "$@"
    EOS
    chmod 0755, bin/"unienv"
    bin.install_symlink "unienv" => "ue"
  end

  def caveats
    <<~EOS
      Finish setting up UniEnv AI's shared environment and AI tool wiring:
        unienv install
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/unienv --version")
  end
end
