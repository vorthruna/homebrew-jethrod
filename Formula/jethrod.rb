class Jethrod < Formula
  desc "Novelty daemon: alerts you when Jethro Tull books North American shows"
  homepage "https://github.com/vorthruna/homebrew-jethrod"
  url "https://github.com/vorthruna/homebrew-jethrod.git",
      tag:      "v0.1.0",
      revision: "9af086e84d457bfd57738c4a160989c578a2afc3"
  license "MIT"
  head "https://github.com/vorthruna/homebrew-jethrod.git", branch: "main"

  uses_from_macos "python"

  def install
    bin.install "jethrod"
    pkgshare.install "tests"
  end

  service do
    run [opt_bin/"jethrod", "run"]
    keep_alive true
    environment_variables PATH: std_service_path_env
    log_path var/"log/jethrod.log"
    error_log_path var/"log/jethrod.log"
  end

  def caveats
    <<~EOS
      Start listening (runs at login):
        brew services start jethrod
      Phone pushes via the free ntfy app:
        jethrod config ntfy_topic <your-secret-topic> && jethrod test-alert
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/jethrod --version")
    assert_match "ok", shell_output("#{bin}/jethrod selftest")
    ENV["XDG_STATE_HOME"] = testpath
    ENV["JETHROD_FEED"] = "file://#{pkgshare}/tests/feed_2026-11-13.json"
    ENV["JETHROD_DESKTOP"] = "off"
    assert_match "none in North America", shell_output("#{bin}/jethrod check")
  end
end
