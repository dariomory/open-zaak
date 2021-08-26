# SPDX-License-Identifier: EUPL-1.2
# Copyright (C) 2019 - 2020 Dimpact
import copy


def clone_object(instance):
    cloned = copy.deepcopy(instance)  # don't alter original instance
    cloned.pk = None
    try:
        delattr(cloned, "_prefetched_objects_cache")
    except AttributeError:
        pass
    return cloned
