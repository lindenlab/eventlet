# Issue #223: test threading.Thread.join with monkey-patching
import eventlet

# no standard tests in this file, ignore
__test__ = False


if __name__ == '__main__':
    eventlet.monkey_patch()

    import threading
    import time

    sleeper = threading.Thread(target=time.sleep, args=(1,))
    start = time.time()
    sleeper.start()
    sleeper.join()
    dt = time.time() - start

    if dt < 1.0:
        raise Exception("test failed: dt=%s" % dt)

    print('pass')
