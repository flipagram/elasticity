from setuptools import setup

setup(

    # general meta
    name='elasticity',
    version='0.5',
    author='Brian C. Dilley - Flipagram',
    author_email='brian@flipagram.com',
    description='Python based command line tool for managing ElasticSearch clusters.',
    platforms='any',
    url='https://github.com/Cheers-Dev/elasticity',
    download_url='https://github.com/Cheers-Dev/elasticity',

    # packages
    packages=[
        'elasticity'
    ],

    # dependencies
    install_requires=[
        'elasticsearch>=1.4.0',
        'pyyaml>=3.10'
    ],
    # additional files to include
    include_package_data=True,

    # the scripts
    scripts=['scripts/elasticity'],

    # wut?
    classifiers=['Intended Audience :: Developers']
)
