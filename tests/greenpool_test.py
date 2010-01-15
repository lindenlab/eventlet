import gc
import itertools
import os
import random 

import eventlet
from eventlet import api
from eventlet import hubs, greenpool, coros, greenthread
import tests

class Spawn(tests.LimitedTestCase):
    # TODO: move this test elsewhere
    def test_simple(self):
        def f(a, b=None):
            return (a,b)
        
        gt = eventlet.spawn(f, 1, b=2)
        self.assertEquals(gt.wait(), (1,2))

def passthru(a):
    eventlet.sleep(0.01)
    return a
    
def passthru2(a, b):
    eventlet.sleep(0.01)
    return a,b
        
class GreenPool(tests.LimitedTestCase):
    def test_spawn(self):
        p = greenpool.GreenPool(4)
        waiters = []
        for i in xrange(10):
            waiters.append(p.spawn(passthru, i))
        results = [waiter.wait() for waiter in waiters]
        self.assertEquals(results, list(xrange(10)))

    def test_spawn_n(self):
        p = greenpool.GreenPool(4)
        results_closure = []
        def do_something(a):
            eventlet.sleep(0.01)
            results_closure.append(a)
        for i in xrange(10):
            p.spawn(do_something, i)
        p.waitall()
        self.assertEquals(results_closure, range(10))
        
    def test_waiting(self):
        pool = greenpool.GreenPool(1)
        done = greenthread.Event()
        def consume():
            done.wait()
        def waiter(pool):
            gt = pool.spawn(consume)
            gt.wait()
        
        waiters = []
        self.assertEqual(pool.running(), 0)
        waiters.append(eventlet.spawn(waiter, pool))
        eventlet.sleep(0)
        self.assertEqual(pool.waiting(), 0)
        waiters.append(eventlet.spawn(waiter, pool))
        eventlet.sleep(0)
        self.assertEqual(pool.waiting(), 1)
        waiters.append(eventlet.spawn(waiter, pool))
        eventlet.sleep(0)
        self.assertEqual(pool.waiting(), 2)
        self.assertEqual(pool.running(), 1)
        done.send(None)
        for w in waiters:
            w.wait()
        self.assertEqual(pool.waiting(), 0)
        self.assertEqual(pool.running(), 0)
        
    def test_multiple_coros(self):
        evt = greenthread.Event()
        results = []
        def producer():
            results.append('prod')
            evt.send()
        def consumer():
            results.append('cons1')
            evt.wait()
            results.append('cons2')

        pool = greenpool.GreenPool(2)
        done = pool.spawn(consumer)
        pool.spawn_n(producer)
        done.wait()
        self.assertEquals(['cons1', 'prod', 'cons2'], results)

    def test_timer_cancel(self):
        # this test verifies that local timers are not fired 
        # outside of the context of the spawn
        timer_fired = []
        def fire_timer():
            timer_fired.append(True)
        def some_work():
            hubs.get_hub().schedule_call_local(0, fire_timer)
        pool = greenpool.GreenPool(2)
        worker = pool.spawn(some_work)
        worker.wait()
        eventlet.sleep(0)
        eventlet.sleep(0)
        self.assertEquals(timer_fired, [])
        
    def test_reentrant(self):
        pool = greenpool.GreenPool(1)
        def reenter():
            waiter = pool.spawn(lambda a: a, 'reenter')
            self.assertEqual('reenter', waiter.wait())

        outer_waiter = pool.spawn(reenter)
        outer_waiter.wait()

        evt = greenthread.Event()
        def reenter_async():
            pool.spawn_n(lambda a: a, 'reenter')
            evt.send('done')

        pool.spawn_n(reenter_async)
        self.assertEquals('done', evt.wait())
        
    def assert_pool_has_free(self, pool, num_free):
        def wait_long_time(e):
            e.wait()
        timer = api.exc_after(1, api.TimeoutError)
        try:
            evt = greenthread.Event()
            for x in xrange(num_free):
                pool.spawn(wait_long_time, evt)
                # if the pool has fewer free than we expect,
                # then we'll hit the timeout error
        finally:
            timer.cancel()

        # if the runtime error is not raised it means the pool had
        # some unexpected free items
        timer = api.exc_after(0, RuntimeError)
        try:
            self.assertRaises(RuntimeError, pool.spawn, wait_long_time, evt)
        finally:
            timer.cancel()

        # clean up by causing all the wait_long_time functions to return
        evt.send(None)
        eventlet.sleep(0)
        eventlet.sleep(0)
        
    def test_resize(self):
        pool = greenpool.GreenPool(2)
        evt = greenthread.Event()
        def wait_long_time(e):
            e.wait()
        pool.spawn(wait_long_time, evt)
        pool.spawn(wait_long_time, evt)
        self.assertEquals(pool.free(), 0)
        self.assertEquals(pool.running(), 2)
        self.assert_pool_has_free(pool, 0)

        # verify that the pool discards excess items put into it
        pool.resize(1)
        
        # cause the wait_long_time functions to return, which will
        # trigger puts to the pool
        evt.send(None)
        eventlet.sleep(0)
        eventlet.sleep(0)
        
        self.assertEquals(pool.free(), 1)
        self.assertEquals(pool.running(), 0)
        self.assert_pool_has_free(pool, 1)

        # resize larger and assert that there are more free items
        pool.resize(2)
        self.assertEquals(pool.free(), 2)
        self.assertEquals(pool.running(), 0)
        self.assert_pool_has_free(pool, 2)
        
    def test_pool_smash(self):
        # The premise is that a coroutine in a Pool tries to get a token out
        # of a token pool but times out before getting the token.  We verify
        # that neither pool is adversely affected by this situation.
        from eventlet import pools
        pool = greenpool.GreenPool(1)
        tp = pools.TokenPool(max_size=1)
        token = tp.get()  # empty out the pool
        def do_receive(tp):
            timer = api.exc_after(0, RuntimeError())
            try:
                t = tp.get()
                self.fail("Shouldn't have recieved anything from the pool")
            except RuntimeError:
                return 'timed out'
            else:
                timer.cancel()

        # the spawn makes the token pool expect that coroutine, but then
        # immediately cuts bait
        e1 = pool.spawn(do_receive, tp)
        self.assertEquals(e1.wait(), 'timed out')

        # the pool can get some random item back
        def send_wakeup(tp):
            tp.put('wakeup')
        gt = eventlet.spawn(send_wakeup, tp)

        # now we ask the pool to run something else, which should not
        # be affected by the previous send at all
        def resume():
            return 'resumed'
        e2 = pool.spawn(resume)
        self.assertEquals(e2.wait(), 'resumed')

        # we should be able to get out the thing we put in there, too
        self.assertEquals(tp.get(), 'wakeup')
        gt.wait()
        
    def test_spawn_n_2(self):
        p = greenpool.GreenPool(2)
        self.assertEqual(p.free(), 2)
        r = []
        def foo(a):
            r.append(a)
        gt = p.spawn(foo, 1)
        self.assertEqual(p.free(), 1)
        gt.wait()
        self.assertEqual(r, [1])
        eventlet.sleep(0)
        self.assertEqual(p.free(), 2)

        #Once the pool is exhausted, spawning forces a yield.
        p.spawn_n(foo, 2)
        self.assertEqual(1, p.free())
        self.assertEqual(r, [1])

        p.spawn_n(foo, 3)
        self.assertEqual(0, p.free())
        self.assertEqual(r, [1])

        p.spawn_n(foo, 4)
        self.assertEqual(set(r), set([1,2,3]))
        eventlet.sleep(0)
        self.assertEqual(set(r), set([1,2,3,4]))

    def test_imap(self):
        p = greenpool.GreenPool(4)
        result_list = list(p.imap(passthru, xrange(10)))
        self.assertEquals(result_list, list(xrange(10)))
        
    def test_empty_imap(self):
        p = greenpool.GreenPool(4)
        result_iter = p.imap(passthru, [])
        self.assertRaises(StopIteration, result_iter.next)
        
    def test_imap_nonefunc(self):
        p = greenpool.GreenPool(4)
        result_list = list(p.imap(None, xrange(10)))
        self.assertEquals(result_list, [(x,) for x in xrange(10)])
        
    def test_imap_multi_args(self):
        p = greenpool.GreenPool(4)
        result_list = list(p.imap(passthru2, xrange(10), xrange(10, 20)))
        self.assertEquals(result_list, list(itertools.izip(xrange(10), xrange(10,20))))

    def test_imap_raises(self):
        # testing the case where the function raises an exception;
        # both that the caller sees that exception, and that the iterator
        # continues to be usable to get the rest of the items
        p = greenpool.GreenPool(4)
        def raiser(item):
            if item == 1 or item == 7:
                raise RuntimeError("intentional error")
            else:
                return item
        it = p.imap(raiser, xrange(10))
        results = []
        while True:
            try:
                results.append(it.next())
            except RuntimeError:
                results.append('r')
            except StopIteration:
                break
        self.assertEquals(results, [0,'r',2,3,4,5,6,'r',8,9])
        
            
class GreenPile(tests.LimitedTestCase):
    def test_pile(self):
        p = greenpool.GreenPile(4)
        for i in xrange(10):
            p.spawn(passthru, i)
        result_list = list(p)
        self.assertEquals(result_list, list(xrange(10)))
        
    def test_pile_spawn_times_out(self):
        p = greenpool.GreenPile(4)
        for i in xrange(4):
            p.spawn(passthru, i)
        # now it should be full and this should time out
        api.exc_after(0, api.TimeoutError)
        self.assertRaises(api.TimeoutError, p.spawn, passthru, "time out")
        # verify that the spawn breakage didn't interrupt the sequence
        # and terminates properly
        for i in xrange(4,10):
            p.spawn(passthru, i)
        self.assertEquals(list(p), list(xrange(10)))
        
    def test_constructing_from_pool(self):
        pool = greenpool.GreenPool(2)
        pile1 = greenpool.GreenPile(pool)
        pile2 = greenpool.GreenPile(pool)
        def bunch_of_work(pile, unique):
            for i in xrange(10):
                pile.spawn(passthru, i + unique)
        eventlet.spawn(bunch_of_work, pile1, 0)
        eventlet.spawn(bunch_of_work, pile2, 100)
        eventlet.sleep(0)
        self.assertEquals(list(pile2), list(xrange(100,110)))
        self.assertEquals(list(pile1), list(xrange(10)))


class StressException(Exception):
    pass

r = random.Random(0)
def pressure(arg):
    while r.random() < 0.5:
        eventlet.sleep(r.random() * 0.001)
    if r.random() < 0.8:
        return arg
    else:
        raise StressException(arg)

def passthru(arg):
    while r.random() < 0.5:
        eventlet.sleep(r.random() * 0.001)
    return arg
        
class Stress(tests.LimitedTestCase):
    # tests will take extra-long
    TEST_TIMEOUT=10
    @tests.skip_unless(os.environ.get('RUN_STRESS_TESTS') == 'YES')
    def spawn_order_check(self, concurrency):
        # checks that piles are strictly ordered
        p = greenpool.GreenPile(concurrency)
        def makework(count, unique):            
            for i in xrange(count):
                token = (unique, i)
                p.spawn(pressure, token)
        
        iters = 1000
        eventlet.spawn(makework, iters, 1)
        eventlet.spawn(makework, iters, 2)
        eventlet.spawn(makework, iters, 3)
        p.spawn(pressure, (0,0))
        latest = [-1] * 4
        received = 0
        it = iter(p)
        while True:
            try:
                i = it.next()
            except StressException, exc:
                i = exc[0]
            except StopIteration:
                break
            received += 1                
            if received % 5 == 0:
                api.sleep(0.0001)
            unique, order = i
            self.assert_(latest[unique] < order)
            latest[unique] = order
        for l in latest[1:]:
            self.assertEquals(l, iters - 1)

    @tests.skip_unless(os.environ.get('RUN_STRESS_TESTS') == 'YES')
    def test_ordering_5(self):
        self.spawn_order_check(5)
    
    @tests.skip_unless(os.environ.get('RUN_STRESS_TESTS') == 'YES')
    def test_ordering_50(self):
        self.spawn_order_check(50)
    
    def imap_memory_check(self, concurrency):
        # checks that imap is strictly
        # ordered and consumes a constant amount of memory
        p = greenpool.GreenPool(concurrency)
        count = 1000
        it = p.imap(passthru, xrange(count))
        latest = -1
        while True:
            try:
                i = it.next()
            except StopIteration:
                break

            if latest == -1:
                gc.collect()
                initial_obj_count = len(gc.get_objects())
            self.assert_(i > latest)
            latest = i
            if latest % 5 == 0:
                api.sleep(0.001)
            if latest % 10 == 0:
                gc.collect()
                objs_created = len(gc.get_objects()) - initial_obj_count
                self.assert_(objs_created < 25 * concurrency, objs_created)
        # make sure we got to the end
        self.assertEquals(latest, count - 1)
 
    @tests.skip_unless(os.environ.get('RUN_STRESS_TESTS') == 'YES')
    def test_imap_50(self):
        self.imap_memory_check(50)
        
    @tests.skip_unless(os.environ.get('RUN_STRESS_TESTS') == 'YES')
    def test_imap_500(self):
        self.imap_memory_check(500)

    @tests.skip_unless(os.environ.get('RUN_STRESS_TESTS') == 'YES')
    def test_with_intpool(self):
        from eventlet import pools
        class IntPool(pools.Pool):
            def create(self):
                self.current_integer = getattr(self, 'current_integer', 0) + 1
                return self.current_integer
        
        def subtest(intpool_size, pool_size, num_executes):        
            def run(int_pool):
                token = int_pool.get()
                eventlet.sleep(0.0001)
                int_pool.put(token)
                return token
            
            int_pool = IntPool(max_size=intpool_size)
            pool = greenpool.GreenPool(pool_size)
            for ix in xrange(num_executes):
                pool.spawn(run, int_pool)
            pool.waitall()
            
        subtest(4, 7, 7)
        subtest(50, 75, 100)
        for isize in (10, 20, 30, 40, 50):
            for psize in (5, 25, 35, 50):
                subtest(isize, psize, psize)