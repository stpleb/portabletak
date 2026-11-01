package gov.takx.api.annotation;
import java.lang.annotation.*;
@Target(ElementType.METHOD) @Retention(RetentionPolicy.RUNTIME) public @interface InvokeOnEDT { }
