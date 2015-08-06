PIO=/bin/cpio
FIND=/usr/bin/find

all:

update:
	dch -i --check-dirname-level 0 $(MSG) && cd ..

debs deb:
	debuild --no-tgz-check -uc -us -i -I.git -I.gitignore -I.hgignore

install:
	python setup.py install --root=${DESTDIR} --no-compile

cleanall: clean
	debuild clean
	rm -Rf build/

clean:
	# phony target
