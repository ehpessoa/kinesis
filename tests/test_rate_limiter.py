from src.vision.rate_limiter import RateLimiter


def test_rate_limiter_approximates_target_fps():
    rl = RateLimiter(target_fps=10.0)  # min_interval = 0.1s
    t = 0.0
    runs = 0
    for _ in range(50):
        t += 0.02  # simula captura a 50 fps
        if rl.should_run(now=t):
            runs += 1
    assert 9 <= runs <= 11


def test_rate_limiter_zero_fps_means_no_throttle():
    rl = RateLimiter(target_fps=0)
    assert all(rl.should_run(now=t) for t in (0.0, 0.001, 0.002, 100.0))


def test_rate_limiter_high_fps_runs_every_call_at_normal_camera_rate():
    rl = RateLimiter(target_fps=1000.0)
    t = 0.0
    for _ in range(10):
        t += 1.0 / 30.0  # 30 fps de captura
        assert rl.should_run(now=t) is True


def test_rate_limiter_uses_wall_clock_not_call_count():
    rl = RateLimiter(target_fps=1.0)
    assert rl.should_run(now=10.0) is True
    assert rl.should_run(now=10.1) is False
    assert rl.should_run(now=10.9) is False
    assert rl.should_run(now=11.1) is True
