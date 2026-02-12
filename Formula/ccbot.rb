class Ccbot < Formula
  include Language::Python::Virtualenv

  desc "Telegram bot bridging Telegram topics to Claude Code sessions via tmux"
  homepage "https://github.com/alexei-led/ccbot"
  url "https://pypi.io/packages/source/c/ccbot/ccbot-0.2.0.tar.gz"
  sha256 "UPDATE_WITH_ACTUAL_SHA256_AFTER_PYPI_PUBLISH"
  license "MIT"

  depends_on "python@3.14"
  depends_on "tmux"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "usage", shell_output("#{bin}/ccbot --help 2>&1", 0)
  end
end
