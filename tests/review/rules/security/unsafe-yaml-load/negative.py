import yaml

stream = open("config.yml")
safe = yaml.safe_load(stream)
explicit = yaml.load(stream, Loader=yaml.SafeLoader)
