import app, app.config
def test_probe():
    print("PROBE", app.__file__, app.config.__file__)
